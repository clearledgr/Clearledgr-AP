/**
 * site.js, tiny shared client behaviour for soldenai.com.
 *
 * Loaded from every page. Intentionally framework-free: the marketing
 * site is static HTML by design.
 *
 * Two responsibilities:
 *   1. Stamp the current year into any `[data-year]` element.
 *   2. Wire the contact form (if present), POST to /api/contact and
 *      flip into success or error state in place.
 */

(function () {
  // ── 1. Year stamps ──
  var yearNodes = document.querySelectorAll('[data-year]');
  var year = String(new Date().getFullYear());
  for (var i = 0; i < yearNodes.length; i++) {
    yearNodes[i].textContent = year;
  }

  // ── 2. Contact form ──
  var form = document.querySelector('form[data-contact]');
  if (!form) return;

  var submitBtn = form.querySelector('button[type="submit"]');
  var submitOriginalLabel = submitBtn ? submitBtn.innerHTML : '';

  function setSubmitting(on) {
    if (!submitBtn) return;
    if (on) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Sending…';
      form.classList.add('is-submitting');
    } else {
      submitBtn.disabled = false;
      submitBtn.innerHTML = submitOriginalLabel;
      form.classList.remove('is-submitting');
    }
  }

  function collectFields() {
    var data = {};
    for (var i = 0; i < form.elements.length; i++) {
      var el = form.elements[i];
      if (!el.name) continue;
      data[el.name] = el.value;
    }
    return data;
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();

    // Honeypot, silently flip into success state, do NOT send.
    var hp = form.querySelector('input[name="company_website"]');
    if (hp && hp.value) {
      form.classList.add('is-sent');
      form.classList.remove('is-error');
      return;
    }

    form.classList.remove('is-error');
    setSubmitting(true);

    var payload = collectFields();
    delete payload['form-name']; // legacy field; harmless if missing

    fetch('/api/contact', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (json) {
          return { status: res.status, ok: res.ok, json: json };
        });
      })
      .then(function (out) {
        if (out.ok && out.json && out.json.ok) {
          form.classList.add('is-sent');
        } else {
          form.classList.add('is-error');
          setSubmitting(false);
        }
      })
      .catch(function () {
        form.classList.add('is-error');
        setSubmitting(false);
      });
  });
})();
