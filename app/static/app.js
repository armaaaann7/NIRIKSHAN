const state = {
  buildings: [],
  selectedAnalysis: null,
  currentLocation: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHTML(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.className = 'toast show' + (error ? ' error' : '');
  setTimeout(() => { element.className = 'toast'; }, 3400);
}

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  if (!response.ok) {
    let data;
    try { data = await response.json(); } catch { data = { detail: await response.text() }; }
    const detail = typeof data.detail === 'string'
      ? data.detail
      : (data.detail?.message || data.message || JSON.stringify(data.detail));
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function bandClass(band) {
  if (band?.startsWith('Urgent')) return 'Urgent';
  if (band === 'High') return 'High';
  if (band?.startsWith('Review')) return 'Review';
  return 'Routine';
}

function showView(id) {
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === id));
  $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === id));
  const titles = {
    dashboard: 'Inspection overview',
    buildings: 'Monitored buildings',
    inspect: 'New inspection',
    about: 'Method & limits',
  };
  $('#pageTitle').textContent = titles[id] || 'NIRIKSHAN';
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

$$('[data-view]').forEach((element) => element.addEventListener('click', () => showView(element.dataset.view)));
$$('[data-open="buildingModal"]').forEach((button) => button.addEventListener('click', () => $('#buildingModal').showModal()));

const buildingModal = $('#buildingModal');
const closeBuildingModalButton = $('#closeBuildingModal');
const cancelBuildingModalButton = $('#cancelBuildingModal');

if (closeBuildingModalButton && buildingModal) closeBuildingModalButton.addEventListener('click', () => buildingModal.close());
if (cancelBuildingModalButton && buildingModal) cancelBuildingModalButton.addEventListener('click', () => buildingModal.close());
if (buildingModal) {
  buildingModal.addEventListener('click', (event) => {
    if (event.target === buildingModal) buildingModal.close();
  });
}

function formatRecordedAt(value) {
  if (!value) return 'Time unavailable';
  const normalized = value.includes('T') ? value : value.replace(' ', 'T') + 'Z';
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function selectedBuilding() {
  const id = Number($('#inspectionBuilding')?.value || 0);
  return state.buildings.find((building) => Number(building.id) === id) || null;
}

function monitoredAreas(building) {
  return (building?.latest_captures || []).map((capture) => ({
    label: capture.capture_point,
    capture,
  }));
}

async function refresh() {
  const [summary, buildings] = await Promise.all([api('/api/summary'), api('/api/buildings')]);
  state.buildings = buildings;

  const stats = [
    ['Buildings', summary.building_count],
    ['Recorded comparisons', summary.analysis_count],
    ['Recapture required', summary.recapture_count],
    ['High priority', summary.high_count],
    ['Urgent review', summary.urgent_count],
  ];
  $('#stats').innerHTML = stats
    .map(([label, value]) => `<div class="stat"><b>${Number(value) || 0}</b><span>${escapeHTML(label)}</span></div>`)
    .join('');

  renderQueue(summary.top_queue);
  renderBuildings();
  populateBuildingSelect();
}

function renderQueue(items) {
  if (!items.length) {
    $('#queueTable').innerHTML = `
      <div class="empty-state" style="min-height:180px">
        <h3>No engineer-review items yet</h3>
        <p>Record a first photo of a building area, then return later and photograph the same area again.</p>
      </div>`;
    return;
  }

  const rows = items.map((analysis) => {
    const buildingName = escapeHTML(analysis.building?.name || '');
    const ward = escapeHTML(analysis.building?.ward || '');
    const area = escapeHTML(analysis.inspection_area || analysis.capture_point || '');
    const band = escapeHTML(analysis.band || '');
    const reviewStatus = escapeHTML(analysis.review_status || 'Pending');
    const score = Number(analysis.score || 0).toFixed(1);
    const alignment = Math.round((Number(analysis.metrics?.alignment_confidence) || 0) * 100);
    const recordedAt = escapeHTML(formatRecordedAt(analysis.created_at));

    return `<tr>
      <td><strong>${buildingName}</strong><br><small>${ward || 'Ward not entered'}</small></td>
      <td>${area}</td>
      <td><span class="score-pill band-${bandClass(analysis.band)}"><i class="dot"></i>${score} · ${band}</span></td>
      <td>${alignment}%</td>
      <td><div class="queue-meta"><strong>Inspection #${Number(analysis.id)}</strong><small>${recordedAt}</small></div></td>
      <td>${reviewStatus}</td>
      <td>
        <div class="queue-actions">
          <button class="text-button" data-analysis-id="${Number(analysis.id)}">Open →</button>
          <button class="danger-button" data-delete-analysis="${Number(analysis.id)}" title="Remove this inspection from the active queue">Delete</button>
        </div>
      </td>
    </tr>`;
  }).join('');

  $('#queueTable').innerHTML = `
    <table>
      <thead><tr><th>Building</th><th>Inspection area</th><th>Priority</th><th>Alignment</th><th>Recorded</th><th>Review</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;

  $$('#queueTable [data-analysis-id]').forEach((button) => {
    button.addEventListener('click', () => openAnalysis(Number(button.dataset.analysisId)));
  });

  $$('#queueTable [data-delete-analysis]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = Number(button.dataset.deleteAnalysis);
      if (!window.confirm(`Remove Inspection #${id} from the active review queue? The audit evidence will still be retained.`)) return;
      try {
        const result = await api(`/api/analyses/${id}`, { method: 'DELETE' });
        toast(result.message || `Inspection #${id} removed`);
        if (state.selectedAnalysis?.id === id) state.selectedAnalysis = null;
        await refresh();
      } catch (error) {
        toast(error.message, true);
      }
    });
  });
}

function renderBuildings() {
  const grid = $('#buildingGrid');
  if (!state.buildings.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1;min-height:260px">
        <h3>No building records yet</h3>
        <p>Register an identifiable building or society, then record the first photo of one area.</p>
      </div>`;
    return;
  }

  grid.innerHTML = state.buildings.map((building) => {
    const ward = escapeHTML(building.ward || 'Ward not entered');
    const name = escapeHTML(building.name || '');
    const address = escapeHTML(building.address || '');
    const latestBand = escapeHTML(building.overall_band || building.latest_band || 'No reliable analysis');
    const latestScore = building.overall_score == null ? '—' : Number(building.overall_score).toFixed(1);
    const areaCount = Number(building.monitored_area_count || 0);
    const pin = building.pincode ? ` · ${escapeHTML(building.pincode)}` : '';
    const locationTag = building.location_available ? '<span class="location-tag">GPS saved</span>' : '';
    const structureType = escapeHTML(building.structure_type || 'Unknown');
    const floors = building.floor_count ? `${Number(building.floor_count)} floors` : 'Floors not entered';
    const strengthStatus = escapeHTML(building.structural_strength_status || 'Not assessed');
    const driver = building.priority_driver ? escapeHTML(building.priority_driver) : 'No priority-driving area yet';

    return `<article class="building-card" data-building-id="${Number(building.id)}">
      <div class="building-card-top"><p class="eyebrow">${ward}${pin}</p>${locationTag}</div>
      <h4>${name}</h4>
      <p>${address}</p>
      <div class="structure-chip-row"><span>${structureType}</span><span>${escapeHTML(floors)}</span><span>Strength: ${strengthStatus}</span></div>
      <div class="area-count"><strong>${areaCount}</strong><span>${areaCount === 1 ? 'monitored area' : 'monitored areas'}</span></div>
      <div class="card-foot">
        <div><span class="eyebrow">overall inspection priority</span><div class="card-score">${latestScore}</div></div>
        <span class="band">${latestBand}</span>
      </div>
      <div class="priority-driver"><span>Priority driver</span><strong>${driver}</strong></div>
    </article>`;
  }).join('');

  $$('#buildingGrid [data-building-id]').forEach((card) => {
    card.addEventListener('click', () => selectBuilding(Number(card.dataset.buildingId)));
  });
}

function populateBuildingSelect() {
  const select = $('#inspectionBuilding');
  if (!select) return;
  const oldValue = select.value;
  select.innerHTML = '<option value="">Select building…</option>' + state.buildings
    .map((building) => `<option value="${Number(building.id)}">${escapeHTML(building.name)} — ${escapeHTML(building.address)}</option>`)
    .join('');
  if (oldValue && state.buildings.some((b) => String(b.id) === String(oldValue))) select.value = oldValue;
  updateInspectionContext();
}

function selectBuilding(id) {
  showView('inspect');
  $('#inspectionBuilding').value = String(id);
  updateInspectionContext();
}

async function openAnalysis(id) {
  const analysis = await api('/api/analyses/' + id);
  state.selectedAnalysis = analysis;
  renderAnalysis(analysis);
  showView('inspect');
  $('#inspectionBuilding').value = String(analysis.building_id);
  updateInspectionContext();
  $('#existingArea').value = analysis.inspection_area || analysis.capture_point;
  updateInspectionContext();
}

function renderBuildingIdentity(building) {
  const card = $('#buildingIdentity');
  if (!building) {
    card.classList.add('hidden');
    card.innerHTML = '';
    return;
  }
  const location = building.location_available
    ? `GPS ${Number(building.latitude).toFixed(5)}, ${Number(building.longitude).toFixed(5)}`
    : 'GPS not stored';
  const context = [
    building.structure_type || 'Unknown structure',
    building.floor_count ? `${Number(building.floor_count)} floors` : 'floors not entered',
    building.primary_material || 'material unknown',
  ].join(' · ');
  card.innerHTML = `
    <div><span>Selected building</span><strong>${escapeHTML(building.name)}</strong><small>${escapeHTML(building.address)}${building.pincode ? ` · ${escapeHTML(building.pincode)}` : ''}</small><small class="identity-context">${escapeHTML(context)}</small></div>
    <div class="identity-location">${escapeHTML(location)}<small>Structural strength: ${escapeHTML(building.structural_strength_status || 'Not assessed')}</small></div>`;
  card.classList.remove('hidden');
}

function populateAreas(building) {
  const select = $('#existingArea');
  const areas = monitoredAreas(building);
  const old = select.value;
  select.innerHTML = '<option value="__new__">+ Add a new inspection area</option>' + areas
    .map(({ label }) => `<option value="${escapeHTML(label)}">${escapeHTML(label)}</option>`)
    .join('');

  if (old && [...select.options].some((opt) => opt.value === old)) {
    select.value = old;
  } else if (areas.length) {
    select.value = areas[0].label;
  } else {
    select.value = '__new__';
  }
}

function currentExistingCapture() {
  const building = selectedBuilding();
  const area = $('#existingArea')?.value || '__new__';
  if (!building || area === '__new__') return null;
  return (building.latest_captures || []).find((capture) => capture.capture_point === area) || null;
}

function buildNewAreaLabel() {
  const type = $('#areaType').value;
  const floor = $('#floorLevel').value;
  const note = $('#areaNote').value.trim();
  const parts = [type];
  if (floor && floor !== 'Not applicable') parts.push(floor);
  if (note) parts.push(note);
  return parts.join(' · ');
}

function updateAreaPreview() {
  const preview = $('#areaPreview');
  if (!preview) return;
  preview.innerHTML = `New area: <strong>${escapeHTML(buildNewAreaLabel())}</strong>`;
}

function updateInspectionContext() {
  const building = selectedBuilding();
  renderBuildingIdentity(building);
  populateAreas(building);

  const existingArea = $('#existingArea');
  const hasBuilding = Boolean(building);
  if (!hasBuilding) {
    $('#newAreaFields').classList.remove('hidden');
    $('#referenceHint').textContent = 'Select a building first.';
    $('#recordInspectionButton').textContent = 'Record first inspection photo';
    $('#inspectionImageLabel').textContent = 'Inspection photo';
    $('#inspectionImageHelp').textContent = 'Take or upload a clear exterior photo of the selected area.';
    $('#capturePreview').classList.add('hidden');
    $('#overlayControl').classList.add('hidden');
    return;
  }

  const isNew = existingArea.value === '__new__';
  $('#newAreaFields').classList.toggle('hidden', !isNew);
  const capture = isNew ? null : currentExistingCapture();

  if (isNew) {
    updateAreaPreview();
    $('#referenceHint').innerHTML = '<strong>First visit to this area.</strong> The photo you submit will be saved as the starting visual record. No priority score is created yet.';
    $('#inspectionImageLabel').textContent = 'First inspection photo';
    $('#inspectionImageHelp').textContent = 'Take or upload one clear photo. Future visits to this area will be compared with it automatically.';
    $('#recordInspectionButton').textContent = 'Record first inspection photo';
    $('#previousPreview').removeAttribute('src');
    $('#capturePreview').classList.add('hidden');
    $('#overlayControl').classList.add('hidden');
  } else if (capture) {
    $('#referenceHint').innerHTML = `Earlier photo found · <strong>${escapeHTML(capture.capture_point)}</strong> · recorded ${escapeHTML(formatRecordedAt(capture.captured_at))}.`;
    $('#inspectionImageLabel').textContent = "Today's inspection photo";
    $('#inspectionImageHelp').textContent = 'Photograph roughly the same visible area from a similar position. NIRIKSHAN will align and compare it.';
    $('#recordInspectionButton').textContent = 'Compare with earlier photo';
    $('#previousPreview').src = capture.image_url;
    $('#overlayControl').classList.remove('hidden');
  }
}

$('#inspectionBuilding').addEventListener('change', updateInspectionContext);
$('#existingArea').addEventListener('change', updateInspectionContext);
$('#areaType').addEventListener('change', updateAreaPreview);
$('#floorLevel').addEventListener('change', updateAreaPreview);
$('#areaNote').addEventListener('input', updateAreaPreview);

$('#inspectionImage').addEventListener('change', (event) => {
  const file = event.target.files[0];
  if (!file) {
    $('#capturePreview').classList.add('hidden');
    return;
  }
  $('#newPreview').src = URL.createObjectURL(file);
  const capture = currentExistingCapture();
  if (capture) {
    $('#previousPreview').src = capture.image_url;
    $('#capturePreview').classList.remove('hidden');
    $('#overlayControl').classList.remove('hidden');
  } else {
    $('#capturePreview').classList.add('hidden');
    $('#overlayControl').classList.add('hidden');
  }
});

$('#overlayOpacity').addEventListener('input', (event) => {
  $('#newPreview').style.opacity = (Number(event.target.value) / 100).toString();
});

function validateBuildingForm() {
  const name = $('#bName').value.trim();
  const address = $('#bAddress').value.trim();
  const pincode = $('#bPincode').value.trim();
  if (name.length < 3) throw new Error('Enter an identifiable building or society name of at least 3 characters.');
  if (address.length < 8) throw new Error('Enter a complete enough address or locality to identify the building.');
  if (pincode && !/^\d{6}$/.test(pincode)) throw new Error('PIN code must contain exactly 6 digits.');
}

$('#buildingForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    validateBuildingForm();
    const lat = $('#bLatitude').value.trim();
    const lon = $('#bLongitude').value.trim();
    const building = await api('/api/buildings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: $('#bName').value.trim(),
        address: $('#bAddress').value.trim(),
        ward: $('#bWard').value,
        age_years: $('#bAge').value ? Number($('#bAge').value) : null,
        previous_category: '',
        notes: $('#bNotes').value.trim(),
        pincode: $('#bPincode').value.trim(),
        latitude: lat ? Number(lat) : null,
        longitude: lon ? Number(lon) : null,
        structure_type: $('#bStructureType').value,
        floor_count: $('#bFloorCount').value ? Number($('#bFloorCount').value) : null,
        primary_material: $('#bPrimaryMaterial').value,
        repair_history: $('#bRepairHistory').value,
        previous_engineer_assessment: $('#bEngineerAssessment').value,
        verified_strength_data: $('#bStrengthData').value.trim(),
      }),
    });
    buildingModal.close();
    event.target.reset();
    $('#bLatitude').value = '';
    $('#bLongitude').value = '';
    state.currentLocation = null;
    $('#locationStatus').textContent = 'Location not added. You can still register using a clear address.';
    toast(`${building.name} registered`);
    await refresh();
    $('#inspectionBuilding').value = String(building.id);
    showView('inspect');
    updateInspectionContext();
  } catch (error) {
    toast(error.message, true);
  }
});

$('#detectLocation').addEventListener('click', () => {
  if (!navigator.geolocation) {
    toast('This browser does not provide location access.', true);
    return;
  }

  const button = $('#detectLocation');
  button.disabled = true;
  button.textContent = 'Detecting…';
  $('#locationStatus').innerHTML = '<strong>Requesting location…</strong> Allow location access in the browser if prompted.';

  navigator.geolocation.getCurrentPosition(async (position) => {
    const lat = position.coords.latitude;
    const lon = position.coords.longitude;
    const accuracy = position.coords.accuracy;
    state.currentLocation = { lat, lon, accuracy };
    $('#bLatitude').value = lat.toFixed(7);
    $('#bLongitude').value = lon.toFixed(7);

    try {
      const nearby = await api(`/api/buildings/nearby?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&radius_m=80`);
      if (nearby.length) {
        const closest = nearby[0];
        $('#locationStatus').innerHTML = `
          <strong>Location captured · ±${Math.round(accuracy)} m</strong>
          <span>Registered nearby: <b>${escapeHTML(closest.name)}</b> — ${escapeHTML(closest.address)} (${Math.round(closest.distance_m)} m away).</span>
          <button type="button" class="text-button" id="useNearbyBuilding">Use this existing building instead →</button>`;
        $('#useNearbyBuilding').addEventListener('click', () => {
          buildingModal.close();
          selectBuilding(Number(closest.id));
        });
      } else {
        $('#locationStatus').innerHTML = `<strong>Location captured · ±${Math.round(accuracy)} m</strong><span>No registered building was found within 80 m. Confirm the building name and address below.</span>`;
      }
    } catch {
      $('#locationStatus').innerHTML = `<strong>Location captured · ±${Math.round(accuracy)} m</strong><span>Coordinates saved. Confirm the building name and address below.</span>`;
    } finally {
      button.disabled = false;
      button.textContent = 'Update location';
    }
  }, (error) => {
    $('#locationStatus').innerHTML = `<strong>Location not available.</strong><span>${escapeHTML(error.message || 'Use a clear building name and address instead.')}</span>`;
    button.disabled = false;
    button.textContent = 'Use my location';
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
});

$('#inspectionForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const building = selectedBuilding();
  const image = $('#inspectionImage').files[0];
  if (!building) return toast('Select the building you are inspecting.', true);
  if (!image) return toast('Choose or take an inspection photo.', true);

  const existingArea = $('#existingArea').value;
  const isNew = existingArea === '__new__';
  if (isNew && $('#areaType').value === 'Other' && $('#areaNote').value.trim().length < 3) {
    return toast('Describe where the “Other” inspection area is located.', true);
  }

  const form = new FormData();
  form.append('image', image);
  if (isNew) {
    form.append('area_type', $('#areaType').value);
    form.append('floor_level', $('#floorLevel').value);
    form.append('area_note', $('#areaNote').value.trim());
  } else {
    form.append('existing_area', existingArea);
  }

  const button = $('#recordInspectionButton');
  const previousText = button.textContent;
  button.disabled = true;
  button.textContent = isNew ? 'Recording first photo…' : 'Comparing photos…';

  try {
    const result = await api(`/api/buildings/${building.id}/record-inspection`, { method: 'POST', body: form });
    $('#inspectionImage').value = '';
    $('#capturePreview').classList.add('hidden');

    if (result.mode === 'first_capture') {
      renderFirstCapture(result, building);
      toast(`Capture #${result.capture_id} recorded`);
    } else {
      state.selectedAnalysis = result.analysis;
      renderAnalysis(result.analysis);
      toast(result.analysis.is_reliable
        ? `Inspection #${result.analysis.id} recorded`
        : `Inspection #${result.analysis.id} recorded — recapture required`,
      !result.analysis.is_reliable);
    }

    await refresh();
    $('#inspectionBuilding').value = String(building.id);
    updateInspectionContext();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previousText;
    updateInspectionContext();
  }
});

function renderFirstCapture(result, building) {
  $('#analysisPanel').innerHTML = `
    <div class="first-capture-result">
      <div class="success-mark">✓</div>
      <p class="eyebrow">FIRST INSPECTION PHOTO RECORDED</p>
      <h3>${escapeHTML(result.inspection_area)}</h3>
      <p>${escapeHTML(building.name)} now has a starting visual record for this area. No damage score is produced on the first visit because there is nothing earlier to compare against.</p>
      <div class="record-banner">
        <div><strong>Capture #${Number(result.capture_id)} saved</strong><span>On a future visit, select this same monitored area and upload a new photo. NIRIKSHAN will compare it automatically.</span></div>
        <div class="record-id">#${Number(result.capture_id)}</div>
      </div>
      <img class="first-capture-image" src="${escapeHTML(result.image_url)}" alt="First inspection photo">
      <div class="next-step-box"><strong>What happens next?</strong><span>Return later → choose the same building → choose “${escapeHTML(result.inspection_area)}” → take today’s photo.</span></div>
    </div>`;
}

function pct(value) {
  return `${Math.round((Number(value) || 0) * 100)}%`;
}

function renderAnalysis(analysis) {
  const metrics = analysis.metrics || {};
  const factors = analysis.factors || [];
  const reliable = Boolean(analysis.is_reliable);
  const priorityDisplay = reliable ? Number(analysis.score || 0).toFixed(1) : '—';
  const bandDisplay = escapeHTML(analysis.band || '');
  const buildingName = escapeHTML(analysis.building?.name || '');
  const inspectionArea = escapeHTML(analysis.inspection_area || analysis.capture_point || '');
  const reviewStatus = escapeHTML(analysis.review_status || 'Pending');
  const previousUrl = escapeHTML(analysis.previous_image_url || '');
  const currentUrl = escapeHTML(analysis.current_image_url || '');
  const overlayUrl = escapeHTML(analysis.result_urls?.overlay || '');
  const baselineChange = metrics.baseline_comparison?.crack_change?.normalized_change;
  const previousChange = metrics.previous_comparison?.crack_change?.normalized_change;
  const context = metrics.structural_context || {};
  const newRegions = Number(metrics.new_crack_regions || 0);
  const currentRegions = Number(metrics.current_crack_regions || 0);
  const previousRegions = Number(metrics.previous_crack_regions || 0);
  const prioritySummary = escapeHTML(metrics.priority_summary || '');
  const strengthStatus = escapeHTML(context.structural_strength || 'Not assessed');
  const verifiedStrengthData = escapeHTML(context.verified_strength_data || analysis.building?.verified_strength_data || '');
  const structureType = escapeHTML(context.structure_type || analysis.building?.structure_type || 'Unknown');
  const primaryMaterial = escapeHTML(context.primary_material || analysis.building?.primary_material || 'Unknown');
  const repairHistory = escapeHTML(context.repair_history || analysis.building?.repair_history || 'Unknown');
  const engineerAssessment = escapeHTML(context.previous_engineer_assessment || analysis.building?.previous_engineer_assessment || 'No verified record');
  const elementType = escapeHTML(metrics.inspection_element || 'Other');

  $('#analysisPanel').innerHTML = `
    <div class="result-top">
      <div>
        <p class="eyebrow">INSPECTION AREA · ${inspectionArea}</p>
        <h3>${buildingName}</h3>
        <p class="small-copy">${escapeHTML(formatRecordedAt(analysis.created_at))} · ${reviewStatus}</p>
      </div>
      <div><div class="priority-big">${priorityDisplay}</div><span class="band">${bandDisplay}</span></div>
    </div>

    <div class="priority-explanation ${reliable ? '' : 'recapture'}">
      <strong>${reliable ? 'Why this priority?' : 'Comparison not reliable enough'}</strong>
      <p>${reliable ? prioritySummary : 'The new photo was quarantined. It will not replace the trusted earlier image or influence the active review queue.'}</p>
    </div>

    <div class="record-banner">
      <div><strong>Inspection #${Number(analysis.id)} recorded</strong><span>This comparison is stored in the building history${reliable ? ' and active engineer review queue' : ', but the image was quarantined because the comparison was not reliable'}.</span></div>
      <div class="record-id">#${Number(analysis.id)}</div>
    </div>

    ${metrics.needs_recapture ? '<div class="hint" style="margin-top:14px"><strong>Retake this photo:</strong> the new image was not comparable enough to the earlier reference, so it will not replace the trusted previous photo.</div>' : ''}

    <div class="metric-row">
      <div class="metric"><span>Photo alignment</span><strong>${pct(metrics.alignment_confidence)}</strong></div>
      <div class="metric"><span>New crack-like regions</span><strong>${newRegions}</strong><small>One isolated region is weak evidence.</small></div>
      <div class="metric"><span>Current crack-like regions</span><strong>${currentRegions}</strong><small>Previous: ${previousRegions}</small></div>
      <div class="metric"><span>Visible progression</span><strong>${pct(metrics.priority_crack_change)}</strong></div>
      <div class="metric"><span>Compared with first photo</span><strong>${pct(baselineChange)}</strong></div>
      <div class="metric"><span>Compared with previous visit</span><strong>${pct(previousChange)}</strong></div>
    </div>

    <section class="structural-context-result">
      <div class="structural-context-head">
        <div><p class="eyebrow">STRUCTURAL CONTEXT</p><h4>Context for review — not a strength prediction</h4></div>
        <span class="strength-status">Strength: ${strengthStatus}</span>
      </div>
      <div class="context-grid">
        <div><span>Photographed element</span><strong>${elementType}</strong></div>
        <div><span>Structure type</span><strong>${structureType}</strong></div>
        <div><span>Primary material</span><strong>${primaryMaterial}</strong></div>
        <div><span>Building age</span><strong>${context.age_years == null ? 'Not entered' : `${Number(context.age_years)} years`}</strong></div>
        <div><span>Previous repairs</span><strong>${repairHistory}</strong></div>
        <div><span>Previous engineer assessment</span><strong>${engineerAssessment}</strong></div>
      </div>
      ${verifiedStrengthData ? `<div class="verified-data-box"><span>Verified structural/test information</span><strong>${verifiedStrengthData}</strong></div>` : ''}
      <p class="context-disclaimer">NIRIKSHAN does not calculate structural strength from an image. Structure type and verified records only modify inspection order when visible change is already present.</p>
    </section>

    <div class="evidence-grid">
      <div><p class="eyebrow">Earlier accepted photo</p><img src="${previousUrl}" alt="Earlier accepted inspection photo"></div>
      <div><p class="eyebrow">Current photo</p><img src="${currentUrl}" alt="Current inspection photo"></div>
    </div>

    <div class="overlay-wide"><p class="eyebrow">Change overlay · red = crack-like candidate · amber = experimental surface candidate (not scored)</p><img src="${overlayUrl}" alt="Change overlay"></div>

    <div class="factor-list">
      <p class="eyebrow">Transparent priority factors · 55 / 30 / 15</p>
      ${factors.map((factor) => `<div class="factor factor-expanded">
        <div>
          <strong>${escapeHTML(factor.name)}</strong>
          <small>${escapeHTML(factor.detail || '')}</small>
          <div class="bar"><i style="width:${Math.min(100, Math.max(0, Number(factor.normalized_value || 0) * 100))}%"></i></div>
        </div>
        <span>${(Number(factor.contribution) || 0).toFixed(1)} pts</span>
      </div>`).join('')}
    </div>

    <div class="scoring-rule-box">
      <strong>Important scoring rule</strong>
      <span>One isolated crack-like region cannot produce a High or Urgent result by itself. Priority rises when multiple new regions, repeated growth over time and meaningful structural context accumulate together.</span>
    </div>

    <p class="small-copy" style="margin-top:14px">Alignment: ${escapeHTML(metrics.alignment_method || '')}. ${escapeHTML(metrics.scoring_note || '')} ${escapeHTML(metrics.note || '')}</p>

    <div class="review-box">
      <select id="reviewStatus"><option>Pending</option><option>Confirmed</option><option>False alarm</option><option>Needs site visit</option></select>
      <input id="reviewNote" placeholder="Engineer note">
      <button class="button secondary" id="saveReview">Save review</button>
    </div>`;

  $('#reviewStatus').value = analysis.review_status;
  $('#reviewNote').value = analysis.review_note || '';
  $('#saveReview').onclick = async () => {
    try {
      const updated = await api(`/api/analyses/${analysis.id}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: $('#reviewStatus').value, note: $('#reviewNote').value }),
      });
      renderAnalysis(updated);
      toast('Engineer review saved');
      await refresh();
    } catch (error) {
      toast(error.message, true);
    }
  };
}

$('#seedDemo').addEventListener('click', async () => {
  try {
    const result = await api('/api/demo/seed', { method: 'POST' });
    toast(result.message);
    await refresh();
    if (result.analysis_id) await openAnalysis(result.analysis_id);
  } catch (error) {
    toast(error.message, true);
  }
});

updateAreaPreview();
refresh().catch((error) => toast(error.message, true));
