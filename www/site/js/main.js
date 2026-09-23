(function () {
  "use strict";

  var copyBtn = document.getElementById("copy-invite");
  if (!copyBtn) return;

  copyBtn.addEventListener("click", function () {
    var targetId = copyBtn.getAttribute("data-copy-target");
    var link = document.getElementById(targetId);
    if (!link) return;

    var originalLabel = copyBtn.textContent;

    function showCopied() {
      copyBtn.textContent = "Copied!";
      setTimeout(function () {
        copyBtn.textContent = originalLabel;
      }, 1800);
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link.href).then(showCopied, function () {
        window.prompt("Copy this link:", link.href);
      });
    } else {
      window.prompt("Copy this link:", link.href);
    }
  });
})();
