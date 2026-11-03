// Drawn over Bailey's page while a lesson records: a cursor, a spotlight, captions, a chapter
// label and title cards. Loaded with context.add_init_script, so it is in place before the page's
// own scripts run; studio.py drives it through window.__demo.
//
// Built with the CSSOM only (element.style), never a <style> element or a style="" attribute, so
// Bailey's own CSP (style-src 'self') stays on and the page renders as a person sees it.
(() => {
  if (window.__demo) return;
  const ui = {};
  let target = null, pad = 8;
  const css = (el, style) => (Object.assign(el.style, style), el);
  const make = (tag, style) => css(document.createElement(tag), style || {});
  function ensure() {
    if (ui.root) return true;
    if (!document.body) return false;
    ui.root = make("div", { position: "fixed", inset: "0", pointerEvents: "none", zIndex: "2147483000" });
    ui.spot = make("div", { position: "fixed", left: "0", top: "0", width: "0", height: "0", borderRadius: "12px",
      border: "3px solid #f5a524", boxShadow: "0 0 0 9999px rgba(12,14,22,0.40), 0 0 22px 3px rgba(245,165,36,0.55)",
      opacity: "0", transition: "left .45s ease, top .45s ease, width .45s ease, height .45s ease, opacity .35s ease" });
    ui.ripple = make("div", { position: "fixed", left: "0", top: "0", width: "40px", height: "40px", marginLeft: "-20px",
      marginTop: "-20px", borderRadius: "50%", border: "3px solid #f5a524", opacity: "0" });
    ui.cap = make("div", { position: "fixed", left: "50%", bottom: "30px", transform: "translate(-50%, 14px)",
      maxWidth: "1040px", width: "max-content", padding: "14px 26px", borderRadius: "14px", background: "rgba(16,18,27,0.93)",
      color: "#fff", font: "500 23px/1.38 Inter, system-ui, sans-serif", textAlign: "center",
      boxShadow: "0 12px 32px rgba(0,0,0,.35)", opacity: "0", transition: "opacity .35s ease, transform .35s ease" });
    ui.chip = make("div", { position: "fixed", right: "18px", top: "12px", padding: "7px 15px", borderRadius: "999px",
      background: "rgba(16,18,27,0.88)", color: "#f7c55f", font: "600 15px/1.2 Inter, system-ui, sans-serif",
      opacity: "0", transition: "opacity .35s ease" });
    const NS = "http://www.w3.org/2000/svg";
    ui.cursor = document.createElementNS(NS, "svg");
    ui.cursor.setAttribute("width", "30");
    ui.cursor.setAttribute("height", "30");
    ui.cursor.setAttribute("viewBox", "0 0 28 28");
    css(ui.cursor, { position: "fixed", left: "0", top: "0", transform: "translate(640px,360px)",
      filter: "drop-shadow(0 2px 3px rgba(0,0,0,.45))" });
    const arrow = document.createElementNS(NS, "path");
    arrow.setAttribute("d", "M4 2 L4 22 L9.5 17 L13 25 L16.5 23.5 L13 15.7 L20.5 15.7 Z");
    arrow.setAttribute("fill", "#14161f");
    arrow.setAttribute("stroke", "#ffffff");
    arrow.setAttribute("stroke-width", "1.6");
    arrow.setAttribute("stroke-linejoin", "round");
    ui.cursor.append(arrow);
    ui.card = make("div", { position: "fixed", inset: "0", display: "flex", flexDirection: "column", alignItems: "center",
      justifyContent: "center", gap: "14px", background: "radial-gradient(1200px 600px at 50% 40%, #232842, #0e1018)",
      color: "#fff", opacity: "0", transition: "opacity .5s ease", textAlign: "center" });
    ui.logo = make("img", { width: "84px", height: "91px", marginBottom: "10px" });
    ui.logo.alt = "";
    ui.logo.src = "/static/icon.svg";
    ui.kicker = make("div", { font: "600 18px/1.2 Inter, system-ui, sans-serif", letterSpacing: ".14em",
      textTransform: "uppercase", color: "#f7c55f" });
    ui.title = make("div", { font: "700 54px/1.1 Inter, system-ui, sans-serif", letterSpacing: "-0.02em", maxWidth: "1000px" });
    ui.sub = make("div", { font: "400 24px/1.45 Inter, system-ui, sans-serif", color: "#c9cee0", maxWidth: "900px" });
    ui.card.append(ui.logo, ui.kicker, ui.title, ui.sub);
    ui.root.append(ui.spot, ui.ripple, ui.cap, ui.chip, ui.card, ui.cursor);
    document.documentElement.append(ui.root);
    window.addEventListener("mousemove", (e) => {
      ui.cursor.style.transform = "translate(" + (e.clientX - 4) + "px," + (e.clientY - 2) + "px)";
    }, true);
    window.addEventListener("mousedown", (e) => {
      css(ui.ripple, { left: e.clientX + "px", top: e.clientY + "px" });
      ui.ripple.animate([{ opacity: 0.95, transform: "scale(.35)" }, { opacity: 0, transform: "scale(1.5)" }],
        { duration: 520, easing: "ease-out" });
    }, true);
    const follow = () => {
      if (target) {
        const r = target.getBoundingClientRect();
        css(ui.spot, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px",
          height: r.height + 2 * pad + "px" });
      }
      requestAnimationFrame(follow);
    };
    requestAnimationFrame(follow);
    return true;
  }
  window.__demo = {
    ready: ensure,
    caption(text) {
      ensure();
      if (!text) { css(ui.cap, { opacity: "0", transform: "translate(-50%, 14px)" }); return; }
      ui.cap.textContent = text;
      css(ui.cap, { opacity: "1", transform: "translate(-50%, 0)" });
    },
    chapter(text) {
      ensure();
      ui.chip.textContent = text || "";
      ui.chip.style.opacity = text ? "1" : "0";
    },
    spot(el, padding) {
      ensure();
      target = el; pad = padding == null ? 8 : padding;
      ui.spot.style.opacity = el ? "1" : "0";
    },
    card(spec) {
      ensure();
      if (!spec) { ui.card.style.opacity = "0"; ui.cursor.style.opacity = "1"; return; }
      ui.kicker.textContent = spec.kicker || "";
      ui.title.textContent = spec.title || "";
      ui.sub.textContent = spec.sub || "";
      ui.card.style.opacity = "1";
      ui.cursor.style.opacity = "0";
    },
  };
  document.addEventListener("DOMContentLoaded", ensure);
})();
