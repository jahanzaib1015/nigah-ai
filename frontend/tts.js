(function () {
  var currentAudio = null;
  var currentUrl = null;
  var queue = [];
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

    var tryPlay = function () {
      if (audio.__done || currentAudio !== audio) {
        return;
      }
      audio.play().catch(finish);
    };

    // Wait until the MP3 is buffered enough to play straight through,
    // so audio never starts clipped or stuttering.
    if (audio.readyState >= 4) {
      tryPlay();
    } else {
      audio.addEventListener('canplaythrough', tryPlay, { once: true });
      setTimeout(tryPlay, 1500);
    }
  }

  function playText(text) {
    lastText = text;
    lastStarted = false;
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
    stopCurrent();
    playText(text);
  }

  // Speaks after whatever is playing finishes, so announcements never
  // cut each other off mid-word.
  function queueUrdu(text) {
    if (currentAudio) {
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
})();
