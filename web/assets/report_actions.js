/* 本机报告操作：看板内可用；单独拷走的 HTML 不猜测端口或报告路径。 */
(function () {
  'use strict';
  var OFFLINE = '需要在本工具看板中打开报告，才能定位目录或下载 ZIP。';

  function reportNameFromLocation(loc) {
    if (!loc || loc.protocol !== 'http:' ||
        ['localhost', '127.0.0.1', '::1'].indexOf(loc.hostname) < 0 ||
        loc.pathname !== '/api/raw') return null;
    var name = new URLSearchParams(loc.search || '').get('name');
    if (!name || !name.endsWith('.html') ||
        name.split(/[\\/]/).some(function (part) { return part === '..'; })) return null;
    return name;
  }

  function run(kind, name, notify, fetcher, startDownload) {
    notify = notify || function () {};
    if (!name) {
      notify(OFFLINE, 'err');
      return Promise.resolve(false);
    }
    fetcher = fetcher || window.fetch.bind(window);
    var query = '?name=' + encodeURIComponent(name);
    if (kind === 'folder') {
      return fetcher('/api/open-folder' + query, {method: 'POST'})
        .then(function (response) {
          return response.json().then(function (body) {
            if (!response.ok || !body.ok) throw new Error(body.error || '无法打开报告目录');
            return true;
          });
        })
        .catch(function (error) {
          notify('打开文件夹失败：' + (error.message || '看板服务不可达'), 'err');
          return false;
        });
    }
    if (kind !== 'zip') return Promise.resolve(false);
    // 先做很小的服务可达性检查，再交给浏览器原生下载，避免把 ZIP 读进 JS 内存。
    return fetcher('/api/status', {cache: 'no-store'})
      .then(function (response) {
        if (!response.ok) throw new Error('看板服务不可达');
        var jsonlName = name.replace(/\.html$/, '.jsonl');
        var href = '/api/zip?name=' + encodeURIComponent(jsonlName);
        if (startDownload) startDownload(href);
        else {
          var link = document.createElement('a');
          link.href = href;
          link.download = '';
          document.body.appendChild(link);
          link.click();
          link.remove();
        }
        return true;
      })
      .catch(function (error) {
        notify('下载 ZIP 失败：' + (error.message || '看板服务不可达'), 'err');
        return false;
      });
  }

  window.PerfReportActions = {
    offlineMessage: OFFLINE,
    reportNameFromLocation: reportNameFromLocation,
    run: run
  };
})();
