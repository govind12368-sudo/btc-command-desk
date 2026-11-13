import React, { useState } from "react";
import { Card } from "@/components/desk/ui";
import { Btn } from "@/components/desk/forms";
import { SOUNDS, playSound, stopSound, soundPrefs, saveSoundPrefs } from "@/lib/sound";

const GROUPS = [["Rush", "Rush (alarm)"], ["Ringtone", "Ringtone (like a call)"], ["Instrumental", "Instrumental"]];

/** Choose one of 15 loud alert sounds. Plays in the app; the same files can be set as the Telegram alert sound. */
export function SoundPicker() {
  const [p, setP] = useState(soundPrefs());
  const [playing, setPlaying] = useState(null);
  const choose = (id) => { saveSoundPrefs({ id }); setP(soundPrefs()); };
  const toggle = () => { saveSoundPrefs({ on: !p.on }); setP(soundPrefs()); };
  const preview = async (id) => {
    if (playing === id) { stopSound(); setPlaying(null); return; }
    setPlaying(id);
    await playSound(id);
    setTimeout(() => setPlaying((x) => (x === id ? null : x)), 4700);
  };
  return (
    <Card title="Alert sound" testid="sound-picker"
      sub="15 loud sounds, about 4.6 seconds each. Your choice is saved on this device.">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[13px]">
        <span className="text-slate-300">Play the chosen sound when an alert arrives while the app is open:</span>
        <Btn tone={p.on ? "teal" : "ghost"} onClick={toggle} data-testid="sound-toggle">{p.on ? "On" : "Off"}</Btn>
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {GROUPS.map(([g, label]) => (
          <fieldset key={g} className="rounded-lg border border-[#1e2a4a] p-2">
            <legend className="px-1 text-[12px] font-semibold text-slate-300">{label}</legend>
            {SOUNDS.filter((s) => s.group === g).map((s) => (
              <div key={s.id} className={`flex items-center gap-2 rounded px-2 py-1.5 ${p.id === s.id ? "bg-amber-500/10" : ""}`}>
                <input type="radio" name="alert-sound" id={`snd-${s.id}`} checked={p.id === s.id} onChange={() => choose(s.id)} className="h-4 w-4 accent-amber-500" />
                <label htmlFor={`snd-${s.id}`} className="flex-1 cursor-pointer text-[13px] text-slate-200">{s.name}</label>
                <button type="button" onClick={() => preview(s.id)} aria-label={`${playing === s.id ? "Stop" : "Play"} ${s.name}`} data-testid={`play-${s.id}`}
                  className="rounded border border-[#1e2a4a] px-2 py-0.5 text-[12px] text-slate-200 hover:border-amber-500/50">{playing === s.id ? "■ Stop" : "▶ Play"}</button>
                <a href={s.file} download className="text-[11.5px] text-sky-300 underline-offset-2 hover:underline" aria-label={`Download ${s.name}`}>Save</a>
              </div>
            ))}
          </fieldset>
        ))}
      </div>
      <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-[12.5px] text-slate-300" data-testid="sound-iphone-note">
        <p className="font-semibold text-slate-100">When your iPhone is locked or the app is closed</p>
        <p className="mt-1">iPhone does not let web apps choose their notification sound, so the app's pop-up uses the standard iPhone tone. To hear your chosen loud sound with the phone locked, get the same alerts in Telegram, which allows a custom sound:</p>
        <ol className="mt-1 list-decimal space-y-0.5 pl-5">
          <li>Set up Telegram in <b>Settings → Signal alerts → Set up where alerts go</b> (bot token and chat ID).</li>
          <li>On this page tap <b>Save</b> next to your sound and keep it in the Files app.</li>
          <li>In Telegram open the chat with your bot → tap its name → <b>Notifications → Sound → Upload Sound</b>, pick the file, then select it.</li>
          <li>Keep the iPhone ringer on (side switch) and the volume up.</li>
        </ol>
      </div>
    </Card>
  );
}
