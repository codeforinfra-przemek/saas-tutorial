window.initFranchiseMap = function initFranchiseMap(options) {
    const mapId = options && options.mapId ? options.mapId : "franchise-map";
    const markerScriptId = options && options.markerScriptId ? options.markerScriptId : "franchise-map-markers";
    const mapElement = document.getElementById(mapId);
    const markerElement = document.getElementById(markerScriptId);

    if (!mapElement || !markerElement || !window.L) {
        return;
    }

    let markers = [];
    try {
        markers = JSON.parse(markerElement.textContent || "[]");
    } catch (error) {
        markers = [];
    }

    const map = L.map(mapElement, {
        scrollWheelZoom: true,
    }).setView([52.0693, 19.4803], 6);

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    }).addTo(map);

    const markerLayer = L.layerGroup().addTo(map);

    function renderMarkers(franchiseSlug) {
        const visibleMarkers = franchiseSlug
            ? markers.filter(function (marker) { return marker.franchiseSlug === franchiseSlug; })
            : markers;
        const bounds = [];

        markerLayer.clearLayers();
        visibleMarkers.forEach(function (marker) {
            if (typeof marker.lat !== "number" || typeof marker.lng !== "number") {
                return;
            }

            const popup = [
                "<strong>" + escapeHtml(marker.franchiseName || "") + "</strong>",
                "<span>" + escapeHtml(marker.city || "") + "</span>",
                "<small>" + escapeHtml(marker.category || "") + "</small>",
                '<a href="' + encodeURI(marker.url || "#") + '">Zobacz szczegóły</a>',
            ].join("<br>");

            L.marker([marker.lat, marker.lng]).addTo(markerLayer).bindPopup(popup);
            bounds.push([marker.lat, marker.lng]);
        });

        if (bounds.length) {
            map.fitBounds(bounds, { padding: [36, 36], maxZoom: 12 });
        }
    }

    renderMarkers();
    window.franchiseMapController = {
        filterByFranchise: renderMarkers,
    };

    setTimeout(function () {
        map.invalidateSize();
    }, 100);

    setTimeout(function () {
        map.invalidateSize();
    }, 500);

    window.addEventListener("resize", function () {
        map.invalidateSize();
    });
};

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
