/* Apply stored appearance before the first paint (no flash of the wrong theme). */
(function () {
  var root = document.documentElement;
  var theme = 'archive';
  var glass = false;
  var noSerif = false;
  try {
    theme = localStorage.getItem('kl.theme') === 'modern' ? 'modern' : 'archive';
    glass = localStorage.getItem('kl.glass') === '1';
    noSerif = localStorage.getItem('kl.noSerif') === '1';
  } catch (_) { /* storage can be unavailable in private mode */ }
  root.setAttribute('data-theme', theme);
  root.classList.toggle('no-serif', noSerif);
  root.classList.toggle('glass-on', theme === 'modern' && glass);
})();
