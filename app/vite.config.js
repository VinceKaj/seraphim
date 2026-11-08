import { defineConfig } from "vite";

// Refreshes the DeepStateMap front-line snapshots (if the index is >12h old). Never fails the build: on
// network errors the previous snapshots are kept. While the dev server runs it re-checks every 3 hours,
// so each new daily snapshot appears without a restart.
function frontline() {
  // After new snapshots arrive, extend the time slider's per-day change summary (changes.json).
  const refreshAll = async (log) => {
    await refreshFrontline(log);
    await buildChanges({ log }).catch((e) => log.warn(`[changes] update failed: ${e.message}`));
  };
  return {
    name: "frontline-snapshots",
    async buildStart() {
      await refreshAll(console);
    },
    configureServer(server) {
      const timer = setInterval(() => refreshAll(server.config.logger), 3 * 3600 * 1000);
      server.httpServer?.on("close", () => clearInterval(timer));
    },
  };
}

// Regenerates public/events.geojson + groups.json from events/*.md and groups/*.md on startup and on change.
function eventsFromMarkdown() {
  return {
    name: "events-from-markdown",
    async buildStart() {
      await buildEvents();
    },
    configureServer(server) {
      server.watcher.add([EVENTS_DIR, GROUPS_DIR]);
      const onChange = async (file) => {
        if (![EVENTS_DIR, GROUPS_DIR].some((d) => file.startsWith(d)) || !file.toLowerCase().endsWith(".md")) return;
        await buildEvents({ log: server.config.logger });
        server.ws.send({ type: "full-reload" });
      };
      server.watcher.on("add", onChange).on("change", onChange).on("unlink", onChange);
    },
  };
}

export default defineConfig({
  // seraphim: data comes from trust/export_demo.py; no plugins (they would write into ~/Kacper via symlinks)
  plugins: [],
  cacheDir: ".vite-cache",
  // node_modules is a symlink to ~/Kacper/node_modules: allow Vite to serve files from there (read only)
  server: { fs: { allow: [".", "/home/vince/Kacper/node_modules"] } },
  // MapLibre 6 locates its web worker relative to its own module URL
  // (import.meta.url). Vite's dependency pre-bundling moves it into
  // node_modules/.vite/deps/, where the worker file doesn't exist, so tiles
  // never get processed. Serve it unbundled from its dist folder instead.
  optimizeDeps: { exclude: ["maplibre-gl"] },
});
