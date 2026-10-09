// The delegate theme: the things that CSS and templates cannot do.
// design/README.md is the spec.
(function () {
  // A code block shows its language in the top-right corner.
  document.querySelectorAll(".md-typeset .highlight").forEach(function (block) {
    var match = /\blanguage-([\w-]+)/.exec(block.className);
    if (match) block.setAttribute("data-dlg-lang", match[1]);
  });

  // Each glossary term links its entry. `delegate docs` writes DLG_GLOSSARY, a map from each
  // form of a term to its anchor, from docs/glossary.md. A term in a link or a heading stays as it is.
  var glossary = window.DLG_GLOSSARY || {};
  var config = document.getElementById("__config");
  var base = config ? JSON.parse(config.textContent).base : ".";
  if (!/\/glossary\/?$/.test(location.pathname)) {
    document.querySelectorAll(".md-typeset abbr").forEach(function (abbr) {
      var anchor = glossary[abbr.textContent];
      if (!anchor || abbr.closest("a, h1, h2, h3, h4, h5, h6")) return;
      var link = document.createElement("a");
      link.className = "dlg-term";
      link.href = base + "/glossary/#" + anchor;
      abbr.parentNode.insertBefore(link, abbr);
      link.appendChild(abbr);
    });
  }

  // A diagram sits in a figure box that may scroll sideways on a narrow screen.
  document.querySelectorAll(".md-typeset .mermaid").forEach(function (diagram) {
    var figure = document.createElement("div");
    figure.className = "dlg-figure";
    diagram.parentNode.insertBefore(figure, diagram);
    figure.appendChild(diagram);
  });

  // On a reference page, each table gets the text of the nearest heading before it as a label.
  if (document.querySelector(".dlg-ref")) {
    document.querySelectorAll(".dlg-ref .md-typeset table").forEach(function (table) {
      var anchor = table.closest(".md-typeset__scrollwrap") || table;
      var node = anchor.previousElementSibling;
      while (node && !/^H[23]$/.test(node.tagName)) node = node.previousElementSibling;
      if (!node) return;
      var label = document.createElement("div");
      label.className = "dlg-table-label";
      label.textContent = node.textContent.replace("¶", "").trim();
      anchor.parentNode.insertBefore(label, anchor);
    });
  }

  // The quick-search field in the sidebar opens the search dialog.
  var field = document.querySelector(".dlg-quicksearch");
  var button = document.querySelector(".md-search__button");
  if (field && button) {
    var open = function (event) {
      event.preventDefault();
      button.click();
    };
    field.addEventListener("mousedown", open);
    field.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") open(event);
    });
  }

  // The search dialog is in a shadow root, so the page stylesheet cannot reach it. This
  // style goes into the root. Its class names are the minified names of the Zensical
  // version in .zensical-version; check them again when that version changes.
  //   .l dialog, .p backdrop, .s input, .B count, .b result list, .i result,
  //   .C result body, .D path row, .n path, .x title, .u snippet.
  var SEARCH_CSS = [
    ".e{font-family:var(--dlg-sans);letter-spacing:normal}",
    ".l{background:var(--dlg-bg);border:1px solid var(--dlg-rule);border-radius:4px;box-shadow:none;-webkit-backdrop-filter:none;backdrop-filter:none}",
    ".k{border-bottom:1px solid var(--dlg-rule)}",
    ".s input{font-family:var(--dlg-sans);font-size:16px;letter-spacing:normal;color:var(--dlg-ink)}",
    ".B{font-family:var(--dlg-sans);font-size:14px;color:var(--dlg-muted);padding:0 20px;margin:16px 0 10px}",
    ".b{gap:26px;margin:0 10px 16px;color:var(--dlg-ink-soft)}",
    ".i{padding:6px 10px}",
    ".i:before{background:var(--dlg-side)}",
    ".C{gap:2px}",
    ".D{order:2}",
    ".x{order:1;font-family:var(--dlg-sans);font-weight:600;font-size:18px;line-height:1.35;color:var(--dlg-link)}",
    ".x code{font-family:var(--dlg-mono);font-size:.85em;background:none;padding:0}",
    ".n{font-family:var(--dlg-mono);font-size:12.5px;color:var(--dlg-muted)}",
    ".n li:after{content:\"›\"}",
    ".u{order:3;margin-top:6px;font-family:var(--dlg-serif);font-size:16px;line-height:1.6;color:var(--dlg-ink-soft)}",
    ".u code{font-family:var(--dlg-mono);font-size:.84em;color:var(--dlg-code-ink);background:var(--dlg-code-bg);border-radius:3px;padding:1px 5px}",
    ".i mark{background:var(--dlg-mark);color:var(--dlg-ink);padding:0 2px}",
    ".i u,.m u{text-decoration:none!important}",
  ].join("");

  function styleSearch(host) {
    var root = host.shadowRoot;
    if (!root || root.querySelector("style[data-dlg]")) return;
    var style = document.createElement("style");
    style.setAttribute("data-dlg", "");
    style.textContent = SEARCH_CSS;
    root.appendChild(style);
  }

  function findSearch() {
    for (var i = 0; i < document.body.children.length; i++) {
      if (document.body.children[i].shadowRoot) styleSearch(document.body.children[i]);
    }
  }

  findSearch();
  new MutationObserver(findSearch).observe(document.body, { childList: true });
})();
