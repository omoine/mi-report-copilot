/* The access gate.

   The page ships locked and this script is what unlocks it, so a browser that
   never runs it shows nothing rather than showing everything. The real check is
   on the server: this only decides what is on screen. */

const gateForm = document.getElementById('gateForm');
const gatePassword = document.getElementById('gatePassword');
const gateError = document.getElementById('gateError');
const gateSubmit = document.getElementById('gateSubmit');

function unlock() {
  document.body.classList.remove('locked');
}

// Already signed in from an earlier visit? Then never show the gate at all.
fetch('/api/session')
  .then((r) => r.json())
  .then((d) => { if (d.authenticated) unlock(); })
  .catch(() => { /* stay locked; the form is the way in */ });

gateForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  gateError.textContent = '';
  gateSubmit.disabled = true;
  gateSubmit.textContent = 'Checking…';
  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: gatePassword.value }),
    });
    if (res.ok) {
      // Reloaded rather than revealed, so the rest of the application starts
      // from a clean load with the cookie set and boots exactly as it would
      // for someone who was already signed in.
      location.reload();
      return;
    }
    const data = await res.json().catch(() => ({}));
    gateError.textContent = data.detail || 'That password is not correct.';
  } catch {
    gateError.textContent = 'Could not reach the server. Is it running?';
  }
  gateSubmit.disabled = false;
  gateSubmit.textContent = 'Enter';
  gatePassword.select();
});
