/* 在报告图表和 Label 渲染脚本之后启动新页签入场。 */
(function () {
  if (!document.body || document.body.classList.contains('report-page-reveal')) return;
  requestAnimationFrame(function () {
    document.body.classList.add('report-page-reveal');
  });
})();
