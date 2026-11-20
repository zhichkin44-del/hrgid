/* Окна: закрытие по Esc и по щелчку на затемнение (как в раскладке, SCREENS.md §0 п.2). */
(function () {
  function closeWindow() {
    var x = document.querySelector(".ov .dlg .x");
    if (x) { x.click(); return true; }
    return false;
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && closeWindow()) { e.preventDefault(); }
  });
  document.addEventListener("click", function (e) {
    if (e.target && e.target.classList && e.target.classList.contains("ov")) { closeWindow(); }
  });
})();
