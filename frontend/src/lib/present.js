// Presentation window (owner, 28 Nov 2026): for 4 days the admin pages leave out sentences that say a researched idea
// "did not make money" / "no group … is profitable" and the like. The research itself is unchanged; after the date
// below every sentence is shown again with no deploy needed.
import { useDesk } from "@/context/DeskContext";

export const PRESENT_UNTIL = Date.parse("2026-12-02T07:00:00+05:30");
const NEG = /did not make money|no group of any of them is profitable|coin flip|real edge|\bno edge\b|did not predict|beat luck|none of the tested data reliably/i;

export const presentingNow = () => Date.now() < PRESENT_UNTIL;

export function softenText(text) {
  if (!text || typeof text !== "string") return text;
  const parts = text.split(/(?<=[.!?])\s+/);
  return parts.filter((s) => !NEG.test(s)).join(" ").trim();
}

// soften(text): the text without those sentences while the window is open and an admin is signed in; otherwise unchanged.
export function useSoften() {
  const { isAdmin } = useDesk();
  const on = isAdmin && presentingNow();
  return (text) => (on ? softenText(text) : text);
}

export function usePresenting() {
  const { isAdmin } = useDesk();
  return isAdmin && presentingNow();
}
