(function () {
  var currentAudio = null;
  var currentUrl = null;
  var queue = [];
  var active = false;
  var lastText = null;
  var lastStarted = false;

  function revokeUrl() {
    if (currentUrl) {
      URL.revokeObjectURL(currentUrl);
      currentUrl = null;
    }
  }

  function stopCurrent() {
    if (currentAudio) {
      currentAudio.__done = true;
      currentAudio.pause();
      currentAudio = null;
    }
    revokeUrl();
  }

  function advance() {
    if (queue.length) {
      playText(queue.shift());
    } else {
      active = false;
    }
  }

  function startAudio(blob) {
    stopCurrent();
    currentUrl = URL.createObjectURL(blob);
    var audio = new Audio();
    currentAudio = audio;
    audio.src = currentUrl;
    audio.addEventListener('playing', function () {
      if (currentAudio === audio) {
        lastStarted = true;
      }
    });

    var finish = function () {
      if (audio.__done) {
        return;
      }
      audio.__done = true;
      if (currentAudio === audio) {
        currentAudio = null;
        revokeUrl();
      }
      advance();
    };
    audio.addEventListener('ended', finish);
    audio.addEventListener('error', finish);

    // Never play until the browser confirms the ENTIRE file is loaded
    // (canplaythrough), so playback can never start clipped or cut off.
    audio.addEventListener('canplaythrough', function () {
      if (audio.__done || currentAudio !== audio) {
        return;
      }
      audio.play().catch(finish);
    }, { once: true });
    audio.load();
  }

  var blobCache = {};

  // Pre-generate fixed phrases at page load so they play instantly.
  function preloadUrdu(text) {
    if (blobCache[text]) {
      return;
    }
    fetch('/generate-speech', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error('speech request failed');
        }
        return response.blob();
      })
      .then(function (blob) {
        if (blob) {
          blobCache[text] = blob;
        }
      })
      .catch(function () {});
  }

  function playText(text) {
    active = true;
    lastText = text;
    lastStarted = false;
    var source = blobCache[text]
      ? Promise.resolve(blobCache[text])
      : fetch('/generate-speech', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: text })
        }).then(function (response) {
          if (!response.ok) {
            throw new Error('speech request failed');
          }
          return response.blob();
        });
    source
      .then(function (blob) {
        if (lastText !== text) {
          return;
        }
        startAudio(blob);
      })
      .catch(advance);
  }

  // Cancels anything playing or queued, then speaks immediately.
  function speakUrdu(text) {
    queue = [];
    active = true;
    stopCurrent();
    playText(text);
  }

  // Speaks after whatever is playing finishes, so announcements never
  // cut each other off mid-word.
  function queueUrdu(text) {
    if (active) {
      queue.push(text);
    } else {
      playText(text);
    }
  }

  // Browsers block audio before any user gesture; replay the last
  // announcement on first interaction if it never started.
  function retryIfBlocked() {
    if (lastText && !lastStarted) {
      speakUrdu(lastText);
    }
  }

  document.addEventListener('pointerdown', retryIfBlocked);
  document.addEventListener('keydown', retryIfBlocked);

  window.speakUrdu = speakUrdu;
  window.queueUrdu = queueUrdu;
  window.preloadUrdu = preloadUrdu;
})();
