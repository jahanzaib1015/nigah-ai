(function () {
  var supported = 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;

  var cachedVoice = null;
  var lastText = null;
  var lastStarted = false;

  function scoreVoice(voice) {
    var lang = (voice.lang || '').toLowerCase();
    var name = (voice.name || '').toLowerCase();
    if (lang.indexOf('ur') === 0) return 0;
    if (name.indexOf('urdu') !== -1) return 1;
    if (lang.indexOf('hi') === 0) return 2;
    if (name.indexOf('hindi') !== -1) return 3;
    if (lang === 'pk' || lang.slice(-3) === '-pk') return 4;
    if (name.indexOf('pakistan') !== -1) return 5;
    return -1;
  }

  function refreshVoice() {
    var voices = window.speechSynthesis.getVoices();
    var best = null;
    var bestScore = Infinity;
    for (var i = 0; i < voices.length; i++) {
      var score = scoreVoice(voices[i]);
      if (score !== -1 && score < bestScore) {
        best = voices[i];
        bestScore = score;
      }
    }
    cachedVoice = best;
  }

  function speakUrdu(text) {
    if (!supported) return;
    window.speechSynthesis.cancel();
    lastText = text;
    lastStarted = false;
    if (!cachedVoice) refreshVoice();
    var utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'ur-PK';
    if (cachedVoice) utterance.voice = cachedVoice;
    utterance.onstart = function () {
      lastStarted = true;
    };
    window.speechSynthesis.speak(utterance);
  }

  // Browsers block speech before any user gesture; replay the last
  // announcement on first interaction if it never started.
  function retryIfBlocked() {
    if (lastText && !lastStarted) {
      speakUrdu(lastText);
    }
  }

  if (supported) {
    refreshVoice();
    window.speechSynthesis.addEventListener('voiceschanged', refreshVoice);
    document.addEventListener('pointerdown', retryIfBlocked);
    document.addEventListener('keydown', retryIfBlocked);
  }

  window.speakUrdu = speakUrdu;
})();
