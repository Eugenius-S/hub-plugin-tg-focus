// "Only the important" settings in the Settings tab: handoff silence in the bot and the task digest - hub.json keys
// telegram.focus, telegram.digest, telegram.digest_minutes (core allow-list, labels come from the snapshot).
const KEYS = ["telegram.focus", "telegram.digest", "telegram.digest_minutes"];

// Own string table: the plugin picks by hub.lang at use time; any other language falls back to English.
const STR = {
  en: {
    title: "Only the important",
    hint: "What the Telegram bot sends you",
    old_hub: "This hub does not know the telegram.focus settings - update the hub.",
  },
  ru: {
    title: "Только важное",
    hint: "Что бот Telegram шлёт вам",
    old_hub: "Этот хаб не знает настроек telegram.focus — обновите хаб.",
  },
};

export const t = (hub, key, vars = {}) =>
  String(((STR[hub?.lang] ?? STR.en)[key] ?? STR.en[key] ?? key)).replace(/\{(\w+)\}/g, (m, k) => vars[k] ?? m);

export function rows(snap) {
  const all = Array.isArray(snap?.settings) ? snap.settings : [];
  return KEYS.map((k) => all.find((r) => r.key === k)).filter(Boolean);
}

export default function register(hub) {
  if (typeof hub.addOptions !== "function") return;
  hub.addOptions({
    title: t(hub, "title"),
    hint: t(hub, "hint"),
    render(root, snap) {
      const have = rows(snap);
      if (!have.length) {
        root.textContent = t(hub, "old_hub");
        return;
      }
      for (const r of have) {
        let node = root.querySelector(`[data-key="${r.key}"]`);
        if (!node) {
          node = hub.el("label", "opt");
          node.dataset.key = r.key;
          const input = hub.el("input");
          input.type = r.kind === "num" ? "number" : "checkbox";
          if (r.kind === "num") { input.min = r.min; input.max = r.max; }
          const note = hub.el("span", "hint set-note");
          input.addEventListener("change", async () => {
            const value = r.kind === "num" ? Number(input.value) : input.checked;
            note.textContent = "…";
            const res = await hub.post("/api/settings/set", { key: r.key, value });
            note.textContent = res?.error ? String(res.error) : "";
          });
          const txt = hub.el("span");
          txt.append(document.createTextNode(r.label), ...(r.hint ? [hub.el("small", null, r.hint)] : []), note);
          node.append(input, txt);
          root.append(node);
        }
        const input = node.querySelector("input");
        if (document.activeElement !== input) {
          if (r.kind === "num") input.value = r.value; else input.checked = !!r.value;
        }
      }
    },
  });
}
