(function () {
  var CARE_OPEN_TEXT =
    'کسٹمر کیئر۔ واٹس ایپ پر رابطہ کے لیے پہلا بٹن دبائیں، سادہ کال کے لیے دوسرا بٹن دبائیں۔ بند کرنے کے لیے سب سے نیچے والا بٹن دبائیں۔';

  var sheet = document.createElement('div');
  sheet.className = 'care-sheet hidden';
  sheet.setAttribute('role', 'dialog');
  sheet.setAttribute('aria-modal', 'true');
  sheet.setAttribute('aria-labelledby', 'careTitle');
  sheet.innerHTML =
    '<div class="care-card">' +
    '<h2 class="care-title" id="careTitle">Customer Care</h2>' +
    '<a class="btn btn-care-wa" href="https://wa.me/923001234567" target="_blank" rel="noopener" aria-label="WhatsApp Par Call Karein — 03001234567">' +
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"></path>' +
    '</svg>' +
    'WhatsApp Par Call Karein' +
    '</a>' +
    '<a class="btn btn-care-call" href="tel:03001234567" aria-label="Simple Call Karein — 03001234567">' +
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>' +
    '</svg>' +
    'Simple Call Karein' +
    '</a>' +
    '<button type="button" class="btn btn-care-close" id="careCloseBtn">Band Karein</button>' +
    '</div>';
  document.body.appendChild(sheet);

  var lastTrigger = null;

  function openSheet(trigger) {
    lastTrigger = trigger || null;
    sheet.classList.remove('hidden');
    if (window.speakUrdu) {
      window.speakUrdu(CARE_OPEN_TEXT);
    }
    var first = sheet.querySelector('.btn-care-wa');
    if (first) {
      first.focus();
    }
  }

  function closeSheet() {
    sheet.classList.add('hidden');
    if (lastTrigger) {
      lastTrigger.focus();
      lastTrigger = null;
    }
  }

  document.addEventListener('click', function (event) {
    var trigger = event.target.closest('[data-care-open]');
    if (trigger) {
      event.preventDefault();
      openSheet(trigger);
      return;
    }
    if (event.target === sheet) {
      closeSheet();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !sheet.classList.contains('hidden')) {
      closeSheet();
    }
  });

  var closeBtn = sheet.querySelector('#careCloseBtn');
  if (closeBtn) {
    closeBtn.addEventListener('click', closeSheet);
  }

  if (window.preloadUrdu) {
    window.preloadUrdu(CARE_OPEN_TEXT);
  }
})();
