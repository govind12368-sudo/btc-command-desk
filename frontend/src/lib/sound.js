// Alert sound for this device: chosen sound plays (loud) when a push alert arrives while the app is open.
// iPhone only lets a page play sound after a tap, so the first tap anywhere in the app "unlocks" one audio
// element that is then reused for every alert.
export const SOUNDS = [
  ["rush-1-siren", "Rush", "Siren sweep"], ["rush-2-fast-beeps", "Rush", "Fast beeps"], ["rush-3-klaxon", "Rush", "Klaxon"],
  ["rush-4-red-alert", "Rush", "Red alert"], ["rush-5-buzzer", "Rush", "Buzzer pulses"],
  ["ring-1-classic", "Ringtone", "Classic phone ring"], ["ring-2-double", "Ringtone", "Double ring"], ["ring-3-old-bell", "Ringtone", "Old bell ringer"],
  ["ring-4-digital", "Ringtone", "Digital ring"], ["ring-5-marimba", "Ringtone", "Wooden ring"],
  ["inst-1-piano", "Instrumental", "Piano rise"], ["inst-2-fanfare", "Instrumental", "Brass fanfare"], ["inst-3-chimes", "Instrumental", "Bright chimes"],
  ["inst-4-guitar", "Instrumental", "Guitar strum"], ["inst-5-synth", "Instrumental", "Synth arpeggio"],
].map(([id, group, name]) => ({ id, group, name, file: `/sounds/${id}.mp3` }));

const KEY = "vantage-sound";
let audio = null;
let unlocked = false;

const read = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (_) { return {}; } };
export const soundPrefs = () => ({ id: "rush-1-siren", on: true, ...read() });
export const saveSoundPrefs = (p) => { try { localStorage.setItem(KEY, JSON.stringify({ ...soundPrefs(), ...p })); } catch (_) { /* private mode */ } };

const el = () => {
  if (!audio) { audio = new Audio(); audio.preload = "auto"; audio.volume = 1; }
  return audio;
};

export function unlockAudio() {
  if (unlocked) return;
  const a = el();
  a.src = SOUNDS.find((s) => s.id === soundPrefs().id)?.file || SOUNDS[0].file;
  a.muted = true;
  const p = a.play();
  const done = () => { a.pause(); a.currentTime = 0; a.muted = false; unlocked = true; };
  if (p && p.then) p.then(done).catch(() => { a.muted = false; }); else done();
}

export function playSound(id) {
  const s = SOUNDS.find((x) => x.id === (id || soundPrefs().id)) || SOUNDS[0];
  const a = el();
  a.muted = false;
  a.volume = 1;
  a.src = s.file;
  a.currentTime = 0;
  return a.play().catch(() => false);
}

export function stopSound() { if (audio) { audio.pause(); audio.currentTime = 0; } }

/** Call once at app start: unlock on first tap, play the chosen sound when the service worker reports a push. */
export function installAlertSound() {
  const once = () => { unlockAudio(); window.removeEventListener("pointerdown", once, true); window.removeEventListener("keydown", once, true); };
  window.addEventListener("pointerdown", once, true);
  window.addEventListener("keydown", once, true);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (e) => {
      if (e.data?.type === "vantage-push" && e.data.tag !== "silent" && soundPrefs().on) playSound();
    });
  }
}
