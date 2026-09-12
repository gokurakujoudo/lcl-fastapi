/*
MIT License

Copyright (c) 2026 gokurakujoudo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
/* MIT License. Standalone lexical highlighting for LCL 1; no parser or execution. */
(function (root) {
  "use strict";
  const keywords = new Set("and or not if else for in is true false none True False None raise try except finally assert with as using".split(" "));
  const pattern = /(?<space>\s+)|(?<comment>#[^\r\n]*)|(?<string>(?:[rRbBfF]{1,2})?(?:"""(?:\\[\s\S]|(?!""")[^\\])*?(?:"""|$)|'''(?:\\[\s\S]|(?!''')[^\\])*?(?:'''|$)|"(?:\\[\s\S]|[^"\\\r\n])*(?:"|(?=\r|\n|$))|'(?:\\[\s\S]|[^'\\\r\n])*(?:'|(?=\r|\n|$))))|(?<number>0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?[\d_]+)?)|(?<name>[_\p{ID_Start}][_\p{ID_Continue}]*)|(?<operator>\*\*|\/\/|<<|>>|<=|>=|==|!=|\?\.|\?\?|->|[+\-*\/%@&|^~<>=])|(?<punctuation>[()[\]{},.:])|(?<text>[\s\S])/guy;

  /** Return lossless {kind, text} slices, including unfinished input and whitespace. */
  function tokenize(source) {
    if (typeof source !== "string") throw new TypeError("LCL source must be a string");
    const tokens = [];
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      let kind = Object.keys(match.groups).find(key => match.groups[key] !== undefined);
      if (kind === "name" && keywords.has(match[0])) kind = "keyword";
      tokens.push({kind, text: match[0]});
    }
    return tokens;
  }

  /** Return escaped markup. All source characters are text, never executable HTML. */
  function highlight(source) {
    return tokenize(source).map(({kind, text}) => {
      const escaped = text.replace(/[&<>"']/g, character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[character]);
      return kind === "space" || kind === "text" ? escaped : `<span class="lcl-${kind}">${escaped}</span>`;
    }).join("");
  }

  /** Render a code/pre element without changing the source or evaluating it. */
  function render(element, source = element.textContent) {
    element.innerHTML = highlight(source);
    element.classList.add("lcl-code");
  }

  /** Enhance an existing textarea; call update after assigning value, destroy to detach. */
  function attach(textarea) {
    if (textarea.tagName !== "TEXTAREA") throw new TypeError("Expected a textarea");
    if (textarea.parentElement?.classList.contains("lcl-editor")) throw new Error("Textarea already highlighted");
    const document = textarea.ownerDocument;
    const wrapper = document.createElement("div"), backdrop = document.createElement("pre");
    const originalWrap = textarea.getAttribute("wrap");
    wrapper.className = "lcl-editor";
    backdrop.setAttribute("aria-hidden", "true");
    textarea.before(wrapper);
    wrapper.append(backdrop, textarea);
    textarea.wrap = "off";
    function sync() { backdrop.scrollTop = textarea.scrollTop; backdrop.scrollLeft = textarea.scrollLeft; }
    function update() { render(backdrop, textarea.value + "\n"); sync(); }
    textarea.addEventListener("input", update);
    textarea.addEventListener("scroll", sync);
    update();
    return {
      update,
      destroy() {
        textarea.removeEventListener("input", update);
        textarea.removeEventListener("scroll", sync);
        if (originalWrap === null) textarea.removeAttribute("wrap"); else textarea.setAttribute("wrap", originalWrap);
        wrapper.replaceWith(textarea);
      }
    };
  }

  const api = Object.freeze({tokenize, highlight, render, attach});
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LCLHighlight = api;
})(globalThis);
