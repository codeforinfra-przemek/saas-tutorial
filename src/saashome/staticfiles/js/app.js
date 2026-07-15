window.initFranchiseMap = function initFranchiseMap(options) {
    if (window.matchMedia("(max-width: 767px)").matches) {
        window.franchiseMapController = null;
        return;
    }

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
    const markerIcons = {};

    function markerIcon(color) {
        const markerColor = /^#[0-9a-f]{6}$/i.test(color || "") ? color : "#475569";
        if (!markerIcons[markerColor]) {
            markerIcons[markerColor] = L.divIcon({
                className: "category-map-marker",
                html: '<span class="category-map-marker__pin" style="--marker-color: ' + markerColor + '"></span>',
                iconSize: [30, 38],
                iconAnchor: [15, 36],
                popupAnchor: [0, -34],
            });
        }
        return markerIcons[markerColor];
    }

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
                marker.locationType === "available_area"
                    ? "<small>Obszar demonstracyjny - niepotwierdzona placówka</small>"
                    : "",
                '<a href="' + encodeURI(marker.url || "#") + '">Zobacz szczegóły</a>',
            ].filter(Boolean).join("<br>");

            L.marker([marker.lat, marker.lng], { icon: markerIcon(marker.categoryColor) }).addTo(markerLayer).bindPopup(popup);
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

window.loadFranchiseMap = function loadFranchiseMap(options) {
    if (window.matchMedia("(max-width: 767px)").matches) {
        return;
    }
    if (window.L) {
        window.initFranchiseMap(options);
        return;
    }

    const existingScript = document.querySelector('script[data-leaflet-loader="true"]');
    if (existingScript) {
        existingScript.addEventListener("load", function () {
            window.initFranchiseMap(options);
        }, { once: true });
        return;
    }

    const script = document.createElement("script");
    script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    script.dataset.leafletLoader = "true";
    script.onload = function () {
        window.initFranchiseMap(options);
    };
    document.head.appendChild(script);
};

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function initDirectorySelection() {
    const directory = document.querySelector("[data-directory-selection]");
    if (!directory) {
        return;
    }

    const storageKey = "saashome-directory-comparison";
    const maximumSelections = 4;
    const selectionBar = directory.querySelector("[data-directory-selection-bar]");
    const selectionCount = directory.querySelector("[data-directory-selection-count]");
    const compareButton = directory.querySelector("[data-directory-compare]");
    const selectVisibleButton = directory.querySelector("[data-directory-select-visible]");
    const clearSelectionButton = directory.querySelector("[data-directory-clear-selection]");
    let selectedIds = [];

    try {
        const storedIds = JSON.parse(window.localStorage.getItem(storageKey) || "[]");
        if (Array.isArray(storedIds)) {
            selectedIds = storedIds.map(String).filter(function (value, index, values) {
                return /^\d+$/.test(value) && values.indexOf(value) === index;
            }).slice(0, maximumSelections);
        }
    } catch (error) {
        selectedIds = [];
    }

    function persistSelection() {
        try {
            window.localStorage.setItem(storageKey, JSON.stringify(selectedIds));
        } catch (error) {
            // The directory remains usable when browser storage is unavailable.
        }
    }

    function reorderSelectedRecords() {
        const containers = [
            directory.querySelector(".mobile-records"),
            directory.querySelector(".desktop-table tbody"),
        ];

        containers.forEach(function (container) {
            if (!container) {
                return;
            }
            selectedIds.slice().reverse().forEach(function (id) {
                const record = container.querySelector('[data-directory-franchise-id="' + id + '"]');
                if (record) {
                    container.prepend(record);
                }
            });
        });
    }

    function updateSelectionUI() {
        directory.querySelectorAll("[data-directory-select]").forEach(function (checkbox) {
            checkbox.checked = selectedIds.indexOf(checkbox.value) !== -1;
        });

        if (selectionBar) {
            selectionBar.classList.toggle("hidden", selectedIds.length === 0);
            selectionBar.classList.toggle("flex", selectedIds.length > 0);
        }
        if (selectionCount) {
            selectionCount.textContent = String(selectedIds.length);
        }
        if (compareButton) {
            compareButton.disabled = selectedIds.length < 2;
        }
        if (selectVisibleButton) {
            selectVisibleButton.disabled = selectedIds.length >= maximumSelections || availableFranchiseIds().every(function (id) {
                return selectedIds.indexOf(id) !== -1;
            });
        }
        reorderSelectedRecords();
    }

    function availableFranchiseIds() {
        const ids = [];
        const records = directory.querySelectorAll(".desktop-table [data-directory-franchise-id], .mobile-records [data-directory-franchise-id]");

        records.forEach(function (record) {
            const id = String(record.dataset.directoryFranchiseId || "");
            if (id && ids.indexOf(id) === -1) {
                ids.push(id);
            }
        });

        return ids;
    }

    directory.querySelectorAll("[data-directory-select]").forEach(function (checkbox) {
        checkbox.addEventListener("change", function () {
            const id = checkbox.value;
            if (checkbox.checked) {
                if (selectedIds.indexOf(id) === -1 && selectedIds.length < maximumSelections) {
                    selectedIds.push(id);
                } else if (selectedIds.length >= maximumSelections) {
                    window.alert("Możesz porównać maksymalnie 4 franczyzy jednocześnie.");
                }
            } else {
                selectedIds = selectedIds.filter(function (selectedId) {
                    return selectedId !== id;
                });
            }
            persistSelection();
            updateSelectionUI();
        });
    });

    if (compareButton) {
        compareButton.addEventListener("click", function () {
            if (selectedIds.length < 2) {
                return;
            }
            window.location.href = directory.dataset.compareUrl + "?ids=" + encodeURIComponent(selectedIds.join(","));
        });
    }

    if (selectVisibleButton) {
        selectVisibleButton.addEventListener("click", function () {
            availableFranchiseIds().some(function (id) {
                if (selectedIds.length >= maximumSelections) {
                    return true;
                }
                if (selectedIds.indexOf(id) === -1) {
                    selectedIds.push(id);
                }
                return false;
            });
            persistSelection();
            updateSelectionUI();
        });
    }

    if (clearSelectionButton) {
        clearSelectionButton.addEventListener("click", function () {
            selectedIds = [];
            persistSelection();
            updateSelectionUI();
        });
    }

    let currentSort = "";
    let currentDirection = "asc";

    function valueForSort(record, field) {
        const value = record.dataset["sort" + field.charAt(0).toUpperCase() + field.slice(1)] || "";
        if (field === "name" || field === "data") {
            return value.toLocaleLowerCase();
        }
        const numericValue = Number(value);
        return Number.isFinite(numericValue) ? numericValue : null;
    }

    function compareRecords(left, right, field, direction) {
        const leftValue = valueForSort(left, field);
        const rightValue = valueForSort(right, field);
        const multiplier = direction === "asc" ? 1 : -1;

        if (leftValue === null || leftValue === "") {
            return rightValue === null || rightValue === "" ? 0 : 1;
        }
        if (rightValue === null || rightValue === "") {
            return -1;
        }
        if (leftValue < rightValue) {
            return -1 * multiplier;
        }
        if (leftValue > rightValue) {
            return 1 * multiplier;
        }
        if (field === "network") {
            const leftGrowth = Number(left.dataset.sortGrowth || "0");
            const rightGrowth = Number(right.dataset.sortGrowth || "0");
            return (leftGrowth - rightGrowth) * multiplier;
        }
        return 0;
    }

    function sortRecords(field, direction) {
        [
            directory.querySelector(".mobile-records"),
            directory.querySelector(".desktop-table tbody"),
        ].forEach(function (container) {
            if (!container) {
                return;
            }
            const records = Array.from(container.querySelectorAll("[data-directory-franchise-id]"));
            records.sort(function (left, right) {
                return compareRecords(left, right, field, direction);
            });
            records.forEach(function (record) {
                container.appendChild(record);
            });
        });

        directory.querySelectorAll("[data-directory-sort]").forEach(function (button) {
            const isCurrent = button.dataset.directorySort === field;
            button.setAttribute("aria-sort", isCurrent ? (direction === "asc" ? "ascending" : "descending") : "none");
            const indicator = button.querySelector("span");
            if (indicator) {
                indicator.textContent = isCurrent ? (direction === "asc" ? "↑" : "↓") : "↕";
            }
        });
        reorderSelectedRecords();
    }

    directory.querySelectorAll("[data-directory-sort]").forEach(function (button) {
        button.addEventListener("click", function () {
            const field = button.dataset.directorySort;
            currentDirection = currentSort === field && currentDirection === "asc" ? "desc" : "asc";
            currentSort = field;
            sortRecords(field, currentDirection);
        });
    });

    updateSelectionUI();
}

document.addEventListener("DOMContentLoaded", initDirectorySelection);
