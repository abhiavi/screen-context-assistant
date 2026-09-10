import QtQuick
import WaylandBlur 1.0

// Kept in its own file so a missing/failed-to-build plugin only fails
// this Loader's Component, not the whole engine (see avatar.qml, which
// loads this via Loader gated on the hasWaylandBlur context property).
BackgroundBlur {
    anchors.fill: parent
}
