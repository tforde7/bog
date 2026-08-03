const PAGE_SIZE = 25;
const DATA_URL = "./data/candidates.geojson";
const COMMONAGE_DATA_URL = "./data/candidate_commonage.geojson";
const FORESTRY_DATA_URL = "./data/candidate_forestry.geojson";
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
  commonageBySource: new Map(),
  forestryBySource: new Map(),
  commonageLayer: null,
  forestryLayer: null,
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
  commonageToggle: document.querySelector("#commonage-toggle"),
  commonageToggleLabel: document.querySelector("#commonage-toggle-label"),
  forestryToggle: document.querySelector("#forestry-toggle"),
  forestryToggleLabel: document.querySelector("#forestry-toggle-label"),
  commonageLegend: document.querySelector("#commonage-legend"),
  forestryLegend: document.querySelector("#forestry-legend"),
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

function commonageStyle() {
  return {
    renderer: canvasRenderer,
    color: "#4f2458",
    weight: map.getZoom() >= 13 ? 2.2 : 1.4,
    opacity: 1,
    fillColor: "#9b6aa6",
    fillOpacity: 0.58,
    smoothFactor: 0.25,
  };
}

function forestryStyle() {
  return {
    renderer: canvasRenderer,
    color: "#17482a",
    weight: map.getZoom() >= 13 ? 2.2 : 1.4,
    opacity: 1,
    fillColor: "#2f7d4a",
    fillOpacity: 0.62,
    smoothFactor: 0.25,
  };
}

function candidateFromFeature(feature) {
  const properties = feature.properties;
  return {
    feature,
    rank: Number(properties.rank),
    sourceFid: Number(properties.source_fid),
    county: properties.county,
    easting: Number(properties.itm_easting),
    northing: Number(properties.itm_northing),
    titleHa: Number(properties.title_ha),
    bogHa: Number(properties.bog_ha),
    bogPct: Number(properties.bog_pct),
    bogGeomHa: Number(properties.bog_geom_ha),
    lowSlopePct: Number(properties.low15_pct),
    clearBogHa: Number(properties.clear_bog_ha),
    commonageTitleHa: Number(properties.common_title_ha),
    commonageBogHa: Number(properties.common_bog_ha),
    forestryTitleHa: Number(properties.forest_title_ha),
    forestryBogHa: Number(properties.forest_bog_ha),
    commonageFlag: Number(properties.common_flag) === 1,
    forestryFlag: Number(properties.forest_flag) === 1,
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
        ${hectares.format(candidate.clearBogHa)} ha clear of mapped commonage/private forest
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
        <td class="metric-clear">${hectares.format(candidate.clearBogHa)} ha</td>
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

function removeEvidenceLayer(kind) {
  const layerKey = `${kind}Layer`;
  const layer = state[layerKey];
  if (layer && map.hasLayer(layer)) map.removeLayer(layer);
  state[layerKey] = null;
  elements[`${kind}Legend`].hidden = true;
}

function clearEvidenceLayers() {
  removeEvidenceLayer("commonage");
  removeEvidenceLayer("forestry");
  elements.commonageToggle.checked = false;
  elements.forestryToggle.checked = false;
}

function configureEvidenceToggle(kind, candidate) {
  const isCommonage = kind === "commonage";
  const toggle = elements[`${kind}Toggle`];
  const label = elements[`${kind}ToggleLabel`];
  const featureMap = state[`${kind}BySource`];
  const flagged = isCommonage
    ? candidate.commonageFlag
    : candidate.forestryFlag;
  const available = flagged && featureMap.has(candidate.sourceFid);
  const titleArea = isCommonage
    ? candidate.commonageTitleHa
    : candidate.forestryTitleHa;
  const bogArea = isCommonage
    ? candidate.commonageBogHa
    : candidate.forestryBogHa;
  const name = isCommonage ? "commonage" : "private forest";

  toggle.disabled = !available;
  toggle.checked = false;
  label.classList.toggle("is-available", available);
  label.title = available
    ? `Show ${hectares.format(titleArea)} ha mapped ${name} within this title (${hectares.format(bogArea)} ha within screened bog)`
    : `No mapped ${name} overlap for this candidate`;
}

function updateEvidenceControls(candidate) {
  clearEvidenceLayers();
  configureEvidenceToggle("commonage", candidate);
  configureEvidenceToggle("forestry", candidate);
}

function setEvidenceOverlay(kind, visible) {
  removeEvidenceLayer(kind);
  if (!visible || !state.selectedRank) return;

  const candidate = state.candidates.find(
    (item) => item.rank === state.selectedRank,
  );
  if (!candidate) return;
  const feature = state[`${kind}BySource`].get(candidate.sourceFid);
  if (!feature) return;

  const layer = L.geoJSON(feature, {
    renderer: canvasRenderer,
    style: kind === "commonage" ? commonageStyle : forestryStyle,
    interactive: false,
  }).addTo(map);
  state[`${kind}Layer`] = layer;
  elements[`${kind}Legend`].hidden = false;
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
      <div class="selection-card__metric selection-card__metric--clear">
        <span>Clear bog</span>
        <strong>${hectares.format(candidate.clearBogHa)} ha</strong>
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

  updateEvidenceControls(candidate);
  updateSelectionCard(candidate);
  if (updateHash) history.replaceState(null, "", `#candidate-${candidate.rank}`);
}

function resetSelection() {
  clearEvidenceLayers();
  for (const kind of ["commonage", "forestry"]) {
    const toggle = elements[`${kind}Toggle`];
    const label = elements[`${kind}ToggleLabel`];
    toggle.disabled = true;
    label.classList.remove("is-available");
    label.title = `Select a candidate with mapped ${
      kind === "commonage" ? "commonage" : "private forest"
    }`;
  }
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
  state.commonageLayer?.bringToFront();
  state.forestryLayer?.bringToFront();
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
    if (state.boundariesVisible) {
      candidateLayer.addTo(map);
      state.commonageLayer?.bringToFront();
      state.forestryLayer?.bringToFront();
    } else {
      candidateLayer.removeFrom(map);
    }
  });
  elements.commonageToggle.addEventListener("change", (event) =>
    setEvidenceOverlay("commonage", event.currentTarget.checked),
  );
  elements.forestryToggle.addEventListener("change", (event) =>
    setEvidenceOverlay("forestry", event.currentTarget.checked),
  );
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
    state.commonageLayer?.setStyle(commonageStyle());
    state.forestryLayer?.setStyle(forestryStyle());
  });
}

async function fetchGeoJson(url, label) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${label} data returned ${response.status}.`);
  return response.json();
}

function featuresBySource(geojson, label) {
  const features = new Map();
  geojson.features.forEach((feature) => {
    const sourceFid = Number(feature.properties.source_fid);
    if (!Number.isFinite(sourceFid)) {
      throw new Error(`${label} contains a feature without a valid source_fid.`);
    }
    if (features.has(sourceFid)) {
      throw new Error(`${label} contains duplicate source_fid ${sourceFid}.`);
    }
    features.set(sourceFid, feature);
  });
  return features;
}

async function initialise() {
  try {
    const [geojson, commonageGeojson, forestryGeojson] = await Promise.all([
      fetchGeoJson(DATA_URL, "Candidate"),
      fetchGeoJson(COMMONAGE_DATA_URL, "Commonage overlay"),
      fetchGeoJson(FORESTRY_DATA_URL, "Forestry overlay"),
    ]);
    state.commonageBySource = featuresBySource(
      commonageGeojson,
      "Commonage overlay",
    );
    state.forestryBySource = featuresBySource(
      forestryGeojson,
      "Forestry overlay",
    );
    state.candidates = geojson.features
      .map(candidateFromFeature)
      .sort((a, b) => a.rank - b.rank);
    state.filtered = [...state.candidates];

    const commonageFlags = state.candidates.filter(
      (candidate) => candidate.commonageFlag,
    ).length;
    const forestryFlags = state.candidates.filter(
      (candidate) => candidate.forestryFlag,
    ).length;
    if (commonageFlags !== state.commonageBySource.size) {
      throw new Error(
        `Commonage flag/overlay mismatch: ${commonageFlags} flags and ${state.commonageBySource.size} overlays.`,
      );
    }
    if (forestryFlags !== state.forestryBySource.size) {
      throw new Error(
        `Forestry flag/overlay mismatch: ${forestryFlags} flags and ${state.forestryBySource.size} overlays.`,
      );
    }

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
