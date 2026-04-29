// DELULUREEL — shared app JS

// Auto-refresh session before it expires (every 25 min)
(function() {
  const REFRESH_INTERVAL = 25 * 60 * 1000;
  setInterval(() => {
    fetch('/auth/refresh', { method: 'POST' }).catch(() => {});
  }, REFRESH_INTERVAL);
})();
