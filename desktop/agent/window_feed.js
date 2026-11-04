// SPDX-License-Identifier: GPL-3.0-only
// Persistent KWin script: pushes the list of user windows to the Sinux shell
// (org.sinux.Agent.Windows) whenever windows open, close, focus or change.
var pending = false;
var timer = new QTimer();
timer.interval = 60;
timer.singleShot = true;
timer.timeout.connect(send);

function own(w) {
    var c = w.caption || "";
    return c === "Sinux Pointer" || c === "Sinux Island" || c === "Sinux Dock" || c === "Sinux Launcher" || c === "Sinux Top";
}
function send() {
    var list = [];
    var all = workspace.stackingOrder;
    for (var i = 0; i < all.length; i++) {
        var w = all[i];
        if (!w.normalWindow || w.skipTaskbar || own(w)) continue;
        list.push({id: String(w.internalId).replace(/[{}]/g, "").slice(0, 8), title: w.caption,
                   app: String(w.resourceClass || ""), desktopFile: String(w.desktopFileName || ""),
                   active: w === workspace.activeWindow, minimized: w.minimized});
    }
    callDBus("org.sinux.Agent", "/org/sinux/Agent", "org.sinux.Agent1", "Windows", JSON.stringify(list));
}
function soon() { timer.start(); }
function watch(w) {
    if (!w) return;
    w.captionChanged.connect(soon);
    w.minimizedChanged.connect(soon);
}
workspace.windowAdded.connect(function(w) { watch(w); soon(); });
workspace.windowRemoved.connect(soon);
workspace.windowActivated.connect(soon);
var existing = workspace.stackingOrder;
for (var j = 0; j < existing.length; j++) watch(existing[j]);
soon();
