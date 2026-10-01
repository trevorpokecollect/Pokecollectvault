// Interactive 3D card: tilt toward the pointer with a moving glare, flip to show the back.
(function () {
  document.querySelectorAll("[data-card3d]").forEach(function (root) {
    var inner = root.querySelector(".card3d-inner");
    var glare = root.querySelector(".glare");
    var flipBtn = document.querySelector("[data-flip-for='" + root.id + "']");
    var flipped = false;
    var tx = 0, ty = 0;

    function render() {
      inner.style.transform = "rotateX(" + tx.toFixed(1) + "deg) rotateY(" + (ty + (flipped ? 180 : 0)).toFixed(1) + "deg)";
    }

    root.addEventListener("pointermove", function (e) {
      var r = root.getBoundingClientRect();
      var x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      var y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      tx = (0.5 - y) * 24;
      ty = (x - 0.5) * 30;
      root.classList.add("tilting");
      if (glare) {
        glare.style.background = "radial-gradient(circle at " + (x * 100).toFixed(0) + "% " + (y * 100).toFixed(0) +
          "%, rgba(255,255,255,0.28), rgba(255,255,255,0) 55%)";
      }
      render();
    });

    function reset() {
      tx = 0; ty = 0;
      root.classList.remove("tilting");
      render();
    }
    root.addEventListener("pointerleave", reset);
    root.addEventListener("pointercancel", reset);

    if (flipBtn) {
      flipBtn.addEventListener("click", function () {
        flipped = !flipped;
        flipBtn.textContent = flipped ? "Show front" : "Show back";
        reset();
      });
    }
  });
})();
