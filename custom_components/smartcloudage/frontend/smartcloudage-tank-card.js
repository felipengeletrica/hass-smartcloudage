/**
 * SmartCloudAge tank card (beta).
 *
 * Animated tank for a SmartCloudAge level sensor. The water eases to the
 * level, the surface waves, and each float switch (from the sensor's
 * "points" attribute) is marked on the side, lit while active. The card
 * turns into a warning when the switches disagree ("consistent": false).
 *
 *   type: custom:smartcloudage-tank-card
 *   entity: sensor.caixa_dagua
 *   name: Caixa d'água      # optional
 *   color: "#039be5"        # optional water colour
 *   alert_below: 25         # optional, water turns red at or below this
 */

const TOP = 20;
const HEIGHT = 220;
const LEFT = 30;
const WIDTH = 120;

class SmartCloudAgeTankCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("sensor.") && hass.states[id].attributes.points
    );
    return { entity: entity || "" };
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Defina 'entity' com um sensor de nível SmartCloudAge");
    }
    this._config = config;
    this._built = false;
  }

  getCardSize() {
    return 5;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    if (!this._built) this._build();
    this._update();
  }

  _build() {
    const uid = Math.random().toString(36).slice(2, 8);
    this._clipId = `sca-tank-clip-${uid}`;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; cursor: pointer; }
        .header { display: flex; justify-content: space-between; align-items: baseline; }
        .name { font-size: 1.1em; color: var(--primary-text-color); }
        .value { font-size: 2em; font-weight: 500; color: var(--primary-text-color); }
        .warn { color: var(--error-color, #db4437); font-size: 0.9em; min-height: 1.2em; }
        svg { width: 100%; max-height: 320px; display: block; }
        .water { transition: transform 1.6s cubic-bezier(.4, 0, .2, 1), fill .6s; }
        .wave { animation: sca-wave 3s linear infinite; }
        .wave2 { animation: sca-wave 5s linear infinite reverse; opacity: .45; }
        @keyframes sca-wave { from { transform: translateX(0); } to { transform: translateX(-80px); } }
        @media (prefers-reduced-motion: reduce) {
          .wave, .wave2 { animation: none; }
          .water { transition: none; }
        }
        .tank { fill: none; stroke: var(--secondary-text-color); stroke-width: 3; }
        .glass { fill: var(--secondary-background-color, #eee); opacity: .35; }
        .mark { stroke: var(--divider-color, #999); stroke-dasharray: 4 3; }
        .label { font: 11px sans-serif; fill: var(--secondary-text-color); }
      </style>
      <ha-card>
        <div class="header"><span class="name"></span><span class="value"></span></div>
        <svg viewBox="0 0 250 260" role="img">
          <defs>
            <clipPath id="${this._clipId}">
              <rect x="${LEFT}" y="${TOP}" width="${WIDTH}" height="${HEIGHT}" rx="14" />
            </clipPath>
          </defs>
          <rect class="glass" x="${LEFT}" y="${TOP}" width="${WIDTH}" height="${HEIGHT}" rx="14" />
          <g clip-path="url(#${this._clipId})">
            <g class="water" fill="${this._waterColor(100)}">
              <path class="wave2" d="${this._wavePath(4)}" />
              <path class="wave" d="${this._wavePath(0)}" />
            </g>
          </g>
          <rect class="tank" x="${LEFT}" y="${TOP}" width="${WIDTH}" height="${HEIGHT}" rx="14" />
          <g class="marks"></g>
        </svg>
        <div class="warn"></div>
      </ha-card>`;
    this.shadowRoot.querySelector("ha-card").addEventListener("click", () => {
      const ev = new Event("hass-more-info", { bubbles: true, composed: true });
      ev.detail = { entityId: this._config.entity };
      this.dispatchEvent(ev);
    });
    this._built = true;
    this._marksKey = null;
  }

  _wavePath(phase) {
    // Two periods wider than the tank so the translateX loop is seamless.
    let d = `M${LEFT - 80} ${6 + phase}`;
    for (let x = LEFT - 80, i = 0; x < LEFT + WIDTH + 80; x += 40, i += 1) {
      d += ` q20 ${i % 2 ? 6 : -6} 40 0`;
    }
    return `${d} V${TOP + HEIGHT + 20} H${LEFT - 80} Z`;
  }

  _waterColor(level) {
    const alert = this._config.alert_below;
    if (alert !== undefined && level !== null && level <= Number(alert)) {
      return "var(--error-color, #db4437)";
    }
    return this._config.color || "var(--info-color, #039be5)";
  }

  _yFor(percent) {
    return TOP + HEIGHT * (1 - percent / 100);
  }

  _update() {
    const root = this.shadowRoot;
    const stateObj = this._hass.states[this._config.entity];
    const name = this._config.name || (stateObj && stateObj.attributes.friendly_name) || this._config.entity;
    root.querySelector(".name").textContent = name;

    const raw = stateObj ? Number.parseFloat(stateObj.state) : NaN;
    const level = Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : null;
    root.querySelector(".value").textContent = level === null ? "—" : `${Math.round(level)} %`;

    const water = root.querySelector(".water");
    // Wave crest sits ~6 px above the path origin.
    const y = level === null ? TOP + HEIGHT + 20 : this._yFor(level) - 6;
    water.style.transform = `translateY(${y}px)`;
    water.setAttribute("fill", this._waterColor(level));

    const attrs = (stateObj && stateObj.attributes) || {};
    const points = Array.isArray(attrs.points) ? attrs.points : [];
    const active = new Set(attrs.active_channels || []);
    const key = JSON.stringify([points, [...active]]);
    if (key !== this._marksKey) {
      this._marksKey = key;
      root.querySelector(".marks").innerHTML = points
        .map((p) => {
          const py = this._yFor(p.percent);
          const on = active.has(p.channel);
          const fill = on ? "var(--success-color, #43a047)" : "var(--disabled-text-color, #bdbdbd)";
          return `
            <line class="mark" x1="${LEFT}" x2="${LEFT + WIDTH + 8}" y1="${py}" y2="${py}" />
            <circle cx="${LEFT + WIDTH + 14}" cy="${py}" r="6" fill="${fill}" />
            <text class="label" x="${LEFT + WIDTH + 24}" y="${py + 4}">E${p.channel} · ${p.percent}%</text>`;
        })
        .join("");
    }

    let warn = "";
    if (!stateObj) warn = `Entidade ${this._config.entity} não encontrada`;
    else if (attrs.consistent === false) warn = "⚠ Boias inconsistentes: verifique boia travada";
    else if (level === null) warn = "Aguardando leitura da controladora";
    root.querySelector(".warn").textContent = warn;
  }
}

if (!customElements.get("smartcloudage-tank-card")) {
  customElements.define("smartcloudage-tank-card", SmartCloudAgeTankCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "smartcloudage-tank-card",
    name: "SmartCloudAge Tanque",
    description: "Tanque animado para o nível por boias da SmartCloudAge (beta).",
    preview: true,
  });
}
