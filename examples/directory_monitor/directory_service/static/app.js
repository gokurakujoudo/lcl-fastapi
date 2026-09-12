"use strict";
const $ = id => document.getElementById(id);
let snapshot = {entries: [], errors: [], revision: 0};
function message(text, error = false) { $("message").textContent = text; $("message").classList.toggle("error", error); }
function render(data = snapshot) {
  snapshot = data;
  const entries = data.entries || [], files = entries.filter(e => e.kind === "file");
  $("file-count").textContent = files.length;
  $("dir-count").textContent = entries.length - files.length;
  $("bytes").textContent = files.reduce((sum, e) => sum + e.bytes, 0).toLocaleString();
  $("revision").textContent = data.revision;
  const visible = entries.filter(e => e.path.toLowerCase().includes($("filter").value.toLowerCase()));
  $("entries").replaceChildren();
  visible.forEach(entry => {
    const row = document.createElement("tr");
    [entry.path, entry.kind, entry.bytes.toLocaleString(), new Date(entry.modified_ns / 1e6).toLocaleString()].forEach(value => {
      const cell = row.insertCell(); cell.textContent = value;
    });
    const action = row.insertCell();
    if (entry.kind === "file") {
      const link = document.createElement("a"); link.textContent = "Download";
      link.href = "/api/files/" + entry.path.split("/").map(encodeURIComponent).join("/");
      action.append(link);
    }
    $("entries").append(row);
  });
  $("empty").hidden = visible.length > 0;
  $("empty").textContent = entries.length ? "No paths match this filter." : "This directory is empty. Upload a file to begin.";
  if (data.truncated || data.errors?.length) message([data.truncated ? "Entry limit reached: this snapshot is partial." : "", ...(data.errors || [])].filter(Boolean).join("\n"), true);
}
async function refresh() {
  try { const response = await fetch("/api/files"); if (!response.ok) throw Error("Snapshot unavailable"); render(await response.json()); }
  catch (error) { message(error.message, true); }
}
$("filter").addEventListener("input", () => render());
$("refresh").addEventListener("click", refresh);
$("file").addEventListener("change", () => { if ($("file").files[0]) $("destination").value = $("file").files[0].name; });
$("upload").addEventListener("submit", async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const name = $("destination").value;
    const response = await fetch("/api/files/" + name.split("/").map(encodeURIComponent).join("/"), {method: "PUT", headers: {"Content-Type": "application/octet-stream"}, body: $("file").files[0]});
    const body = await response.json(); if (!response.ok) throw Error(body.detail || "Upload failed");
    message("Created " + name + ". The background scan will publish its statistics shortly.");
  } catch (error) { message(error.message, true); } finally { button.disabled = false; }
});
const events = new EventSource("/api/events");
events.addEventListener("open", () => { $("connection").textContent = "● Live updates connected"; });
events.addEventListener("inventory", event => render(JSON.parse(event.data)));
events.addEventListener("error", () => { $("connection").textContent = "Reconnecting…"; });
window.addEventListener("pagehide", () => events.close());
refresh();
