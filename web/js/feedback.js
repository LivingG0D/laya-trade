// ========================================================
// User feedback: toast notifications + audio alert chimes
// ========================================================

let audioEnabled = true;

export function toggleAudioEnabled() {
  audioEnabled = !audioEnabled;
  return audioEnabled;
}

export function showToast(message, type = "info", duration = null) {
  const toastContainer = document.getElementById("toastContainer");
  if (!toastContainer) return;
  const timeoutMs = duration !== null ? duration : (type === "error" ? 5000 : 2500);

  // Keep at most 4 toasts on screen
  while (toastContainer.children.length >= 4) {
    toastContainer.firstChild.remove();
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  const span = document.createElement("span");
  span.textContent = message;
  toast.appendChild(span);
  toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.classList.add("toast-out");
    setTimeout(() => toast.remove(), 300);
  }, timeoutMs);
}

export function playSignalChime(isLong) {
  if (!audioEnabled) return;
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    const now = ctx.currentTime;

    if (isLong) {
      osc.frequency.setValueAtTime(587.33, now); // D5
      osc.frequency.exponentialRampToValueAtTime(880.0, now + 0.15); // A5
    } else {
      osc.frequency.setValueAtTime(659.25, now); // E5
      osc.frequency.exponentialRampToValueAtTime(440.0, now + 0.15); // A4
    }

    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.3);
    osc.start(now);
    osc.stop(now + 0.3);
  } catch (e) {}
}
