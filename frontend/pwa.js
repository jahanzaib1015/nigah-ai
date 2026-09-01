// Register the service worker from the site root so its scope covers
// every page. Registering from /static/ would scope it to /static/ only.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  });
}
