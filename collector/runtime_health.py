# -*- coding: utf-8 -*-
"""采集运行期健康状态：通道缺失、断连连续计数与实时数据自检。"""

from data_health import check_rows_live


# 连续失败达到 3 轮才请求主调用方执行 ADB 探活。
FAIL_ALERT_STREAK = 3
BACKOFF_MAX_S = 5.0


def backoff_sleep(base_iv, fail_streak):
    """按连续失败轮数计算本轮等待时间。"""
    base = max(0.05, float(base_iv or 0.05))
    streak = int(fail_streak or 0)
    if streak < FAIL_ALERT_STREAK:
        return base
    return min(base * streak, BACKOFF_MAX_S)


def row_has_any_value(row):
    """判断采样行是否已有至少一个有效指标或错误信息。"""
    for key in ("fps", "cpu", "mem", "net", "therm"):
        value = row.get(key)
        if not isinstance(value, dict):
            continue
        if value.get("error"):
            return True
        for field in (
            "fps", "cpu_total_pct", "cpu_proc_pct", "pss_kb", "vmrss_kb",
            "rx_kbps", "tx_kbps", "temp_c", "power_w", "voltage_v",
        ):
            if value.get(field) is not None:
                return True
    return False


class ChannelAlertTracker:
    """通道缺数/断连事件的进入与恢复沿状态机。"""

    DISCONNECT_ERR_COUNT = 4
    MISSING_ERR_COUNT = 3

    def __init__(self):
        self._in_disconnect = False
        self._in_missing = False

    def update(self, err_codes):
        events = []
        count = len(err_codes)
        distribution = {}
        for code in err_codes:
            distribution[code] = distribution.get(code, 0) + 1
        if (self.MISSING_ERR_COUNT <= count < self.DISCONNECT_ERR_COUNT
                and not self._in_missing):
            self._in_missing = True
            events.append({
                "event": "channel_alert", "kind": "missing_metric",
                "detail": {"err_count": count, "err_codes": distribution},
            })
        elif count < self.MISSING_ERR_COUNT and self._in_missing:
            self._in_missing = False
            events.append({
                "event": "channel_alert", "kind": "recovered",
                "detail": {"err_count": count, "scope": "missing"},
            })
        if count >= self.DISCONNECT_ERR_COUNT and not self._in_disconnect:
            self._in_disconnect = True
            events.append({
                "event": "channel_alert", "kind": "disconnect",
                "detail": {"err_count": count, "err_codes": distribution},
            })
        elif count < self.DISCONNECT_ERR_COUNT and self._in_disconnect:
            self._in_disconnect = False
            events.append({
                "event": "channel_alert", "kind": "recovered",
                "detail": {"err_count": count, "scope": "disconnect"},
            })
        return events


class RuntimeHealthTracker:
    """合并每轮通道事件、断连计数与数据健全性状态。

    本类不执行 ADB、不打印也不更新 Web；返回的 probe_required / recovered
    是主调用方执行这些副作用的状态沿。
    """

    def __init__(self, metric_keys, health_check=check_rows_live):
        self._metric_keys = tuple(metric_keys)
        self._disconnect_error_count = max(0, len(self._metric_keys) - 1)
        self._channel_alerts = ChannelAlertTracker()
        self._health_check = health_check
        self._health_streak = {}
        self._fail_streak = 0
        self._diagnostic_active = False

    @property
    def fail_streak(self):
        return self._fail_streak

    def update(self, row):
        err_codes = [
            row[key].get("error")
            for key in self._metric_keys
            if isinstance(row.get(key), dict) and row[key].get("error")
        ]
        channel_events = self._channel_alerts.update(err_codes)

        probe_required = False
        recovered = False
        if len(err_codes) >= self._disconnect_error_count:
            self._fail_streak += 1
            if (self._fail_streak >= FAIL_ALERT_STREAK
                    and not self._diagnostic_active):
                self._diagnostic_active = True
                probe_required = True
        else:
            self._fail_streak = 0
            if self._diagnostic_active:
                self._diagnostic_active = False
                recovered = True

        health_alerts, self._health_streak = self._health_check(
            row, self._health_streak)
        return {
            "err_codes": err_codes,
            "channel_events": channel_events,
            "fail_streak": self._fail_streak,
            "probe_required": probe_required,
            "recovered": recovered,
            "health_alerts": health_alerts,
        }
