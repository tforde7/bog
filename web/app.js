const PAGE_SIZE = 25;
const DATA_URL = "./data/candidates.geojson";
const number = new Intl.NumberFormat("en-IE", {
  maximumFractionDigits: 0,
});
const hectares = new Intl.NumberFormat("en-IE", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const percent = new Intl.NumberFormat("en-IE", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const state = {
  candidates: [],
  filtered: [],
  currentPage: 1,
  selectedRank: null,
  selectedLayer: null,
  boundariesVisible: true,
};

const elements = {
  tableBody: document.querySelector("#candidate-table-body"),
  visibleCount: document.querySelector("#visible-count"),
  searchInput: document.querySelector("#search-input"),
  countySelect: document.querySelector("#county-select"),
  previousPage: document.querySelector("#previous-page"),
  nextPage: document.querySelector("#next-page"),
  pageStatus: document.querySelector("#page-status"),
  emptyResults: document.querySelector("#empty-results"),
  clearFilters: document.querySelector("#clear-filters"),
  boundaryToggle: document.querySelector("#boundary-toggle"),
  resetMap: document.querySelector("#reset-map"),
  mapStatus: document.querySelector("#map-status"),
  selectionCard: document.querySelector("#selection-card"),
  basemapButtons: [...document.querySelectorAll("[data-basemap]")],
};

const streetLayer = L.tileLayer(
  "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  },
);

const satelliteLayer = L.tileLayer(
  "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    maxZoom: 19,
    attribution:
      "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
  },
);

const map = L.map("map", {
  center: [53.45, -8.05],
  zoom: 7,
  layers: [streetLayer],
  preferCanvas: true,
  zoomControl: false,
});

L.control.zoom({ position: "bottomright" }).addTo(map);
const canvasRenderer = L.canvas({ padding: 0.45, tolerance: 6 });
let candidateLayer;
let allBounds;

function baseStyle() {
  return {
    renderer: canvasRenderer,
    color: "#245b4d",
    weight: map.getZoom() >= 12 ? 2 : 1.25,
    opacity: 0.95,
    fillColor: "#6a9d89",
    fillOpacity: 0.26,
    smoothFactor: 0.5,
  };
}

function selectedStyle() {
  return {
    renderer: canvasRenderer,
    color: "#9a5b0b",
    weight: 3,
    opacity: 1,
    fillColor: "#efaa3d",
    fillOpacity: 0.42,
    smoothFactor: 0.35,
  };
}

function candidateFromFeature(feature) {
  const properties = feature.properties;
  return {
    feature,
    rank: Number(properties.rank),
    county: properties.county,
    easting: Number(properties.itm_easting),
    northing: Number(properties.itm_northing),
    titleHa: Number(properties.title_ha),
    bogHa: Number(properties.bog_ha),
    bogPct: Number(properties.bog_pct),
    bogGeomHa: Number(properties.bog_geom_ha),
    lowSlopePct: Number(properties.low15_pct),
    searchText: [
      properties.rank,
      properties.county,
      properties.itm_easting,
      properties.itm_northing,
    ]
      .join(" ")
      .toLowerCase(),
  };
}

function populateCountyFilter() {
  const counties = [...new Set(state.candidates.map((item) => item.county))].sort(
    (a, b) => a.localeCompare(b),
  );
  const options = counties.map((county) => {
    const option = document.createElement("option");
    option.value = county;
    option.textContent = county;
    return option;
  });
  elements.countySelect.append(...options);
}

function popupMarkup(candidate) {
  return `
    <div class="map-popup">
      <div class="map-popup__rank">Candidate ${candidate.rank}</div>
      <div class="map-popup__title">${candidate.county}</div>
      <div class="map-popup__meta">
        ${hectares.format(candidate.bogGeomHa)} ha screened bog ·
        ${percent.format(candidate.lowSlopePct)}% at 0–15% slope
      </div>
    </div>
  `;
}

function buildMapLayer(geojson) {
  candidateLayer = L.geoJSON(geojson, {
    renderer: canvasRenderer,
    style: baseStyle,
    onEachFeature(feature, layer) {
      const rank = Number(feature.properties.rank);
      layer.bindPopup(popupMarkup(state.candidates[rank - 1]), {
        closeButton: false,
        offset: [0, -4],
      });
      layer.on("click", () => selectCandidate(rank, { source: "map" }));
      layer.featureRank = rank;
    },
  }).addTo(map);

  allBounds = candidateLayer.getBounds();
  map.fitBounds(allBounds, { padding: [22, 22] });
}

function renderTable() {
  const totalPages = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
  state.currentPage = Math.min(state.currentPage, totalPages);
  const start = (state.currentPage - 1) * PAGE_SIZE;
  const candidates = state.filtered.slice(start, start + PAGE_SIZE);

  elements.tableBody.replaceChildren(
    ...candidates.map((candidate) => {
      const row = document.createElement("tr");
      row.tabIndex = 0;
      row.dataset.rank = candidate.rank;
      row.setAttribute("aria-label", `Select candidate ${candidate.rank} in ${candidate.county}`);
      if (candidate.rank === state.selectedRank) row.classList.add("is-selected");
      row.innerHTML = `
        <td class="rank-cell">${candidate.rank}</td>
        <td class="county-cell">${candidate.county}</td>
        <td class="coordinate-cell">${number.format(candidate.easting)}, ${number.format(candidate.northing)}</td>
        <td>${hectares.format(candidate.titleHa)} ha</td>
        <td class="metric-primary">${hectares.format(candidate.bogGeomHa)} ha</td>
        <td>${percent.format(candidate.lowSlopePct)}%</td>
      `;
      row.addEventListener("click", () =>
        selectCandidate(candidate.rank, { source: "list" }),
      );
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectCandidate(candidate.rank, { source: "list" });
        }
      });
      return row;
    }),
  );

  elements.visibleCount.textContent = number.format(state.filtered.length);
  elements.pageStatus.textContent = `Page ${state.currentPage} of ${totalPages}`;
  elements.previousPage.disabled = state.currentPage <= 1;
  elements.nextPage.disabled = state.currentPage >= totalPages;
  elements.emptyResults.hidden = state.filtered.length > 0;
}

function applyFilters() {
  const query = elements.searchInput.value.trim().toLowerCase();
  const county = elements.countySelect.value;
  state.filtered = state.candidates.filter(
    (candidate) =>
      (!county || candidate.county === county) &&
      (!query || candidate.searchText.includes(query)),
  );
  state.currentPage = 1;
  renderTable();
}

function pageForCandidate(rank) {
  const filteredIndex = state.filtered.findIndex((item) => item.rank === rank);
  return filteredIndex >= 0 ? Math.floor(filteredIndex / PAGE_SIZE) + 1 : null;
}

function layerForRank(rank) {
  let match = null;
  candidateLayer.eachLayer((layer) => {
    if (layer.featureRank === rank) match = layer;
  });
  return match;
}

function updateSelectionCard(candidate) {
  elements.selectionCard.innerHTML = `
    <div class="selection-card__content">
      <div class="selection-card__identity">
        <div class="selection-card__rank">Candidate ${candidate.rank}</div>
        <h3 class="selection-card__title">${candidate.county}</h3>
        <p class="selection-card__coordinate">ITM ${number.format(candidate.easting)}, ${number.format(candidate.northing)}</p>
      </div>
      <div class="selection-card__metric">
        <span>Full title</span>
        <strong>${hectares.format(candidate.titleHa)} ha</strong>
      </div>
      <div class="selection-card__metric">
        <span>Screened bog</span>
        <strong>${hectares.format(candidate.bogGeomHa)} ha</strong>
      </div>
      <div class="selection-card__metric">
        <span>Low slope</span>
        <strong>${percent.format(candidate.lowSlopePct)}%</strong>
      </div>
      <div class="selection-card__actions">
        <button type="button" id="copy-coordinates">Copy coordinates</button>
        <a href="https://landdirect.ie/" target="_blank" rel="noreferrer">Open Landdirect</a>
      </div>
    </div>
  `;

  document.querySelector("#copy-coordinates").addEventListener("click", async (event) => {
    const value = `${candidate.easting}, ${candidate.northing}`;
    try {
      await navigator.clipboard.writeText(value);
      event.currentTarget.textContent = "Copied";
      window.setTimeout(() => {
        event.currentTarget.textContent = "Copy coordinates";
      }, 1400);
    } catch {
      event.currentTarget.textContent = value;
    }
  });
}

function selectCandidate(rank, { source = "list", updateHash = true } = {}) {
  const candidate = state.candidates.find((item) => item.rank === Number(rank));
  if (!candidate) return;

  if (state.selectedLayer) state.selectedLayer.setStyle(baseStyle());
  state.selectedRank = candidate.rank;
  state.selectedLayer = layerForRank(candidate.rank);
  if (state.selectedLayer) {
    state.selectedLayer.setStyle(selectedStyle());
    state.selectedLayer.bringToFront();
    map.flyToBounds(state.selectedLayer.getBounds(), {
      animate: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      duration: 0.75,
      maxZoom: 15,
      padding: [35, 35],
    });
    if (state.boundariesVisible) state.selectedLayer.openPopup();
  }

  const targetPage = pageForCandidate(candidate.rank);
  if (targetPage) {
    state.currentPage = targetPage;
    renderTable();
    if (source === "map") {
      requestAnimationFrame(() => {
        document
          .querySelector(`tr[data-rank="${candidate.rank}"]`)
          ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    }
  }

  updateSelectionCard(candidate);
  if (updateHash) history.replaceState(null, "", `#candidate-${candidate.rank}`);
}

function resetSelection() {
  if (state.selectedLayer) state.selectedLayer.setStyle(baseStyle());
  state.selectedLayer = null;
  state.selectedRank = null;
  elements.selectionCard.innerHTML = `
    <div class="selection-card__empty">
      <span>Selection</span>
      <p>Choose a candidate from the list or directly on the map.</p>
    </div>
  `;
  renderTable();
}

function setBasemap(name) {
  const useSatellite = name === "satellite";
  if (useSatellite) {
    if (map.hasLayer(streetLayer)) map.removeLayer(streetLayer);
    satelliteLayer.addTo(map);
  } else {
    if (map.hasLayer(satelliteLayer)) map.removeLayer(satelliteLayer);
    streetLayer.addTo(map);
  }
  if (candidateLayer && state.boundariesVisible) candidateLayer.bringToFront();
  elements.basemapButtons.forEach((button) => {
    const active = button.dataset.basemap === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function bindControls() {
  elements.searchInput.addEventListener("input", applyFilters);
  elements.countySelect.addEventListener("change", applyFilters);
  elements.clearFilters.addEventListener("click", () => {
    elements.searchInput.value = "";
    elements.countySelect.value = "";
    applyFilters();
    elements.searchInput.focus();
  });
  elements.previousPage.addEventListener("click", () => {
    state.currentPage -= 1;
    renderTable();
  });
  elements.nextPage.addEventListener("click", () => {
    state.currentPage += 1;
    renderTable();
  });
  elements.boundaryToggle.addEventListener("change", (event) => {
    state.boundariesVisible = event.currentTarget.checked;
    if (state.boundariesVisible) candidateLayer.addTo(map);
    else candidateLayer.removeFrom(map);
  });
  elements.resetMap.addEventListener("click", () => {
    resetSelection();
    map.fitBounds(allBounds, { padding: [22, 22] });
    history.replaceState(null, "", window.location.pathname);
  });
  elements.basemapButtons.forEach((button) =>
    button.addEventListener("click", () => setBasemap(button.dataset.basemap)),
  );
  map.on("zoomend", () => {
    if (!candidateLayer) return;
    candidateLayer.setStyle((feature) =>
      Number(feature.properties.rank) === state.selectedRank
        ? selectedStyle()
        : baseStyle(),
    );
  });
}

async function initialise() {
  try {
    const response = await fetch(DATA_URL);
    if (!response.ok) throw new Error(`Candidate data returned ${response.status}.`);
    const geojson = await response.json();
    state.candidates = geojson.features
      .map(candidateFromFeature)
      .sort((a, b) => a.rank - b.rank);
    state.filtered = [...state.candidates];

    populateCountyFilter();
    buildMapLayer(geojson);
    bindControls();
    renderTable();
    elements.mapStatus.hidden = true;

    const hashRank = Number(window.location.hash.match(/^#candidate-(\d+)$/)?.[1]);
    if (hashRank >= 1 && hashRank <= state.candidates.length) {
      selectCandidate(hashRank, { updateHash: false });
    }
  } catch (error) {
    console.error(error);
    elements.mapStatus.textContent =
      "Candidate data could not be loaded. Please refresh the page.";
  }
}

initialise();
