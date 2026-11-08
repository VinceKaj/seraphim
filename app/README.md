# app — Seraphim demo map

Built on Kacper's map (`~/Kacper`; the code is copied, his folder is untouched). Seraphim additions: `src/seraphim.js` (Polymarket panel, simulated damage layer), `public/stats.html` (scale, validation, source trust).

Data comes from the `trust/` exports:
```bash
uv run trust/export_demo.py         # public/events.geojson (last 30 d of claims), data/h3/predictions.json, stats.json
uv run trust/export_extras.py       # public/polymarket.json, data/damage.geojson (SIMULATED)
uv run trust/export_validation.py   # public/validation.json
```

Run:
```bash
# node_modules, public/assets, public/data/frontline and public/data/ukraine.pmtiles are not in git:
ln -s ~/Kacper/node_modules node_modules
cp -r ~/Kacper/public/assets public/
ln -s ~/Kacper/public/data/frontline public/data/frontline
ln -s ~/Kacper/public/data/ukraine.pmtiles public/data/ukraine.pmtiles
tmux new-session -d -s seraphim-app "cd seraphim/app && npx vite --host 127.0.0.1 --port 5180 --strictPort"
```
(or `npm ci && ./scripts/fetch-tiles.sh` for a fresh copy). The Vite plugins are disabled because they would write into the symlinked folders. On the VM, Caddy serves it at `http://<vm-ip>:443/` (plain HTTP).
