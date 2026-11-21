/* Dialogs: close on Esc and on a click on the overlay (as in the layout, SCREENS.md §0 item 2). */
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
