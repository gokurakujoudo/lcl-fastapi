"use strict";
const $ = id => document.getElementById(id);
let session = sessionStorage.getItem("lcl-session"), report = null, trace = [], cursor = 0, timer = null;
const samples = {
  catalog: ["__LCL_VERSION__: 1\nprice: 12\nquantity: 3\ntotal: price * quantity", "total + 5"],
  conditional: ["__LCL_VERSION__: 1\nchoose_left: True\nleft: 42\nright: 1 / 0\nanswer: left if choose_left else right", "answer"],
  error: ["__LCL_VERSION__: 1\nfirst: second + 1\nsecond: first + 1", "first"]
};
function message(text, error = false) { $("message").textContent = text; $("message").classList.toggle("error", error); }
async function api(path, method = "GET", data) {
  const response = await fetch("/api/sessions" + path, {method, headers: data ? {"Content-Type": "application/json"} : {}, body: data ? JSON.stringify(data) : undefined});
  const body = await response.json();
  if (!response.ok && !body.error) throw Error(typeof body.detail === "string" ? body.detail : "Request rejected");
  return body;
}
function tab(name) {
  document.querySelectorAll("[data-tab]").forEach(button => { const selected = button.dataset.tab === name; button.setAttribute("aria-selected", selected); $(button.dataset.tab).hidden = !selected; });
}
document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => tab(button.dataset.tab)));
function astNode(node) {
  const details = document.createElement("details"), summary = document.createElement("summary"), code = document.createElement("code");
  details.open = true; summary.textContent = node.kind + " · " + node.span.start.line + ":" + node.span.start.column;
  code.textContent = node.source; details.append(summary, code); node.children.forEach(child => details.append(astNode(child))); return details;
}
function drawGraph(edges) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  const sources = [...new Set(edges.map(e => e.source))], targets = [...new Set(edges.map(e => e.target))];
  const height = Math.max(170, Math.max(sources.length, targets.length) * 50 + 30);
  svg.setAttribute("viewBox", `0 0 620 ${height}`); svg.classList.add("graph"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Dependency graph. Complete edges are listed in the following table.");
  edges.forEach(edge => { const y1 = 35 + sources.indexOf(edge.source) * 50, y2 = 35 + targets.indexOf(edge.target) * 50;
    const line = document.createElementNS(ns, "path"); line.setAttribute("d", `M 190 ${y1} C 280 ${y1}, 340 ${y2}, 420 ${y2} m -8 -5 l 8 5 l -8 5`); line.setAttribute("fill", "none"); line.setAttribute("stroke", edge.kind === "eager" ? "#3769b1" : "#b47b36"); if (edge.kind !== "eager") line.setAttribute("stroke-dasharray", "5 3"); svg.append(line); });
  [sources, targets].forEach((names, side) => names.forEach((name, i) => { const text = document.createElementNS(ns, "text"); text.setAttribute("x", side ? "430" : "10"); text.setAttribute("y", 40 + i * 50); text.setAttribute("font-size", "13"); text.setAttribute("fill", "#183049"); text.textContent = name; svg.append(text); }));
  $("graph").replaceChildren(svg);
}
function display(parsed) {
  report = parsed; $("tree").replaceChildren();
  Object.entries({...parsed.ast, "<expression>": parsed.expression_ast}).forEach(([name, node]) => { const label = document.createElement("h3"); label.textContent = name; $("tree").append(label, astNode(node)); });
  $("edges").replaceChildren(); parsed.dependencies.forEach(edge => { const row = document.createElement("tr"); [edge.source, edge.target, edge.kind].forEach(value => { row.insertCell().textContent = value; }); $("edges").append(row); });
  drawGraph(parsed.dependencies); $("evaluate").disabled = false;
}
function stop() { clearInterval(timer); timer = null; $("play").textContent = "Play"; }
function step() {
  $("position").textContent = trace.length ? `${cursor + 1} / ${trace.length}` : "0 / 0";
  $("step").textContent = trace[cursor] || "No events captured."; $("cursor").value = cursor;
  $("previous").disabled = cursor <= 0; $("next").disabled = cursor >= trace.length - 1;
  if (cursor >= trace.length - 1) stop();
}
function evaluation(data) {
  stop(); trace = data.trace || []; cursor = 0;
  $("result").textContent = data.error ? data.error.message : `${data.result.type}: ${data.result.repr}`;
  $("play").disabled = !trace.length; $("cursor").disabled = !trace.length; $("cursor").max = Math.max(0, trace.length - 1); step(); tab("trace");
  message(data.error ? data.error.message : (data.trace_truncated ? "Evaluation complete. Trace display capped at 2,000 events." : "Evaluation complete. Step through the actual native trace."), !!data.error);
}
async function start(fresh = false) {
  try {
    stop(); if (fresh && session) await api("/" + session, "DELETE").catch(() => {});
    let previous = null;
    if (!fresh && session) previous = await api("/" + session).catch(() => null);
    if (!previous) { session = (await api("", "POST")).id; sessionStorage.setItem("lcl-session", session); report = null; $("evaluate").disabled = true; }
    else if (previous.expression_ast) { $("source").value = previous.source; $("expression").value = previous.expression; display(previous); if (previous.evaluation) evaluation(previous.evaluation); }
    $("session-status").textContent = "Session · " + session.slice(0, 8); if (fresh) location.reload();
  } catch (error) { message(error.message, true); }
}
$("parse").addEventListener("click", async () => {
  stop(); $("parse").disabled = true; $("evaluate").disabled = true;
  try { const data = await api("/" + session + "/program", "PUT", {source: $("source").value, expression: $("expression").value});
    if (data.error) { message(data.error.message, true); return; }
    display(data); trace = []; step(); tab("ast"); message("Parsed successfully. Evaluate to record values and cache behavior.");
  } catch (error) { message(error.message, true); } finally { $("parse").disabled = false; }
});
$("evaluate").addEventListener("click", async () => { $("evaluate").disabled = true; try { evaluation(await api("/" + session + "/evaluate", "POST")); } catch (error) { message(error.message, true); } finally { $("evaluate").disabled = !report; } });
[$("source"), $("expression")].forEach(input => input.addEventListener("input", () => { $("evaluate").disabled = true; stop(); message("Source changed. Parse again before evaluating."); }));
$("sample").addEventListener("change", () => { [$("source").value, $("expression").value] = samples[$("sample").value]; $("evaluate").disabled = true; message("Sample loaded. Parse to start a new Frame."); });
$("new-session").addEventListener("click", () => start(true));
$("previous").addEventListener("click", () => { stop(); cursor--; step(); }); $("next").addEventListener("click", () => { stop(); cursor++; step(); });
$("cursor").addEventListener("input", () => { stop(); cursor = Number($("cursor").value); step(); });
$("play").addEventListener("click", () => { if (timer) { stop(); return; } if (cursor >= trace.length - 1) cursor = -1; $("play").textContent = "Pause"; timer = setInterval(() => { cursor++; step(); }, 650); });
start();
