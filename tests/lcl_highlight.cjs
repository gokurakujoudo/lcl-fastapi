"use strict";
const assert = require("node:assert/strict");
const {tokenize, highlight} = require("../examples/playground/playground_service/static/lcl-highlight.js");
for (const source of ["", "# Scope\nx: 12 // 5\n", 'name: f"Hello {user}"', "x: '''multi\nline'''", 'x: "unfinished', "值: True if ready else None", "<script>alert('&')</script>", "x: 0xff + .25e-2", "x: \"\\\"#still string\"", "\r\n\t🎈"]) {
  assert.equal(tokenize(source).map(token => token.text).join(""), source);
  const html = highlight(source);
  assert(!html.includes("<script>"));
}
assert.deepEqual(tokenize("# words\n12 // 5").filter(token => token.kind !== "space").map(token => token.kind), ["comment", "number", "operator", "number"]);
assert.equal(tokenize('f"{user}"')[0].kind, "string");
assert.equal(tokenize("True")[0].kind, "keyword");
assert.equal(tokenize("名字")[0].kind, "name");
assert(highlight("<img onerror='bad'>").includes("&lt;"));
assert.throws(() => tokenize(null), TypeError);
console.log("PASS standalone LCL highlighter: lossless tokens, partial input, operators and escaped markup");
