# -*- coding: utf-8 -*-
"""被测目标与 SurfaceFlinger 渲染层的错配状态判定。"""


class TargetMismatchTracker:
    """纯状态机：对错配进入/变化/恢复沿去重。"""

    WECHAT_PACKAGE = "com.tencent.mm"

    def __init__(self):
        self._message = None

    def _clear(self):
        if self._message is None:
            return None
        self._message = None
        return {"kind": "recovered"}

    def update(self, package, pid, layer_name=None, layer_index=None,
               process_index=None):
        """返回 mismatch/recovered action；状态未变返回 None。"""
        if (package or "").lower() != self.WECHAT_PACKAGE:
            # 热切换到非微信目标后，旧 AppBrand 错配已经失效。
            return self._clear()

        if (layer_index is None or process_index is None
                or layer_index == process_index):
            return self._clear()

        message = (
            f"渲染层 AppBrandUI{layer_index} 与采集进程 "
            f"appbrand{process_index} 不一致（pid={pid}）——cpu/内存可能采错进程"
        )
        if message == self._message:
            return None
        self._message = message
        return {
            "kind": "mismatch",
            "message": message,
            "event": {
                "event": "target_mismatch",
                "layer": layer_name,
                "layer_index": layer_index,
                "pid_index": process_index,
                "pid": pid,
            },
            "status": {
                "layer": layer_name,
                "layer_index": layer_index,
                "pid_index": process_index,
                "pid": pid,
                "message": message,
            },
        }
