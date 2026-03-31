/**
 * WinePriceChart.gs
 *
 * Creates a dynamic time series chart on a "Dashboard" tab.
 * Two dropdowns (Retailer + Reference/Estate) drive a line chart
 * that shows all vintages of the selected estate at the selected
 * retailer over time.
 *
 * HOW TO USE:
 *   1. Paste this file into your Google Apps Script project.
 *   2. Run setupDashboard() once to create the Dashboard + ChartData tabs
 *      and populate the dropdowns.
 *   3. The onEdit trigger fires automatically when either dropdown changes.
 *   4. To re-initialise (e.g. after new retailers/estates are added),
 *      run setupDashboard() again.
 */

const SRC_TAB        = "price_records";
const DATA_TAB       = "ChartData";
const DASH_TAB       = "Dashboard";
const RETAILER_CELL  = "B1";
const REFERENCE_CELL = "B2";

// ── Column names in price_records tab ────────────────────────────────────────
const COL_REFERENCE = "Reference";
const COL_RETAILER  = "Retailer";
const COL_VINTAGE   = "vintage";
const COL_PRICE     = "Price";
const COL_DAY       = "Day";
// ─────────────────────────────────────────────────────────────────────────────


// ── Entry point — run once to set up ─────────────────────────────────────────
function setupDashboard() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // Ensure Dashboard tab exists
  let dash = ss.getSheetByName(DASH_TAB);
  if (!dash) {
    dash = ss.insertSheet(DASH_TAB, 0);
  }
  dash.clearContents();
  dash.clearFormats();

  // Ensure ChartData tab exists (hidden helper)
  let dataSheet = ss.getSheetByName(DATA_TAB);
  if (!dataSheet) {
    dataSheet = ss.insertSheet(DATA_TAB);
    dataSheet.hideSheet();
  }

  // Read unique retailers and references from source
  const { retailers, references } = getUniqueValues_(ss);

  // Labels
  dash.getRange("A1").setValue("Retailer");
  dash.getRange("A2").setValue("Estate");
  dash.getRange("A1:A2").setFontWeight("bold");

  // Retailer dropdown
  const rCell = dash.getRange(RETAILER_CELL);
  rCell.setValue(retailers[0]);
  rCell.setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(retailers).build()
  );

  // Reference dropdown
  const refCell = dash.getRange(REFERENCE_CELL);
  refCell.setValue(references[0]);
  refCell.setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(references).build()
  );

  // Build initial chart
  refreshChart_();

  SpreadsheetApp.getUi().alert("Dashboard ready. Use the dropdowns in B1 and B2 to filter.");
}


// ── onEdit trigger — auto-refresh when either dropdown changes ────────────────
function onEdit(e) {
  if (!e) return;
  const sheet = e.source.getActiveSheet();
  if (sheet.getName() !== DASH_TAB) return;
  const cell = e.range.getA1Notation();
  if (cell !== RETAILER_CELL && cell !== REFERENCE_CELL) return;
  refreshChart_();
}


// ── Core: pivot data → ChartData, rebuild chart ───────────────────────────────
function refreshChart_() {
  const ss        = SpreadsheetApp.getActiveSpreadsheet();
  const dash      = ss.getSheetByName(DASH_TAB);
  const dataSheet = ss.getSheetByName(DATA_TAB);
  const source    = ss.getSheetByName(SRC_TAB);

  const retailer  = dash.getRange(RETAILER_CELL).getValue();
  const reference = dash.getRange(REFERENCE_CELL).getValue();

  if (!retailer || !reference) return;

  // Read source
  const raw     = source.getDataRange().getValues();
  const headers = raw[0];
  const iRef  = headers.indexOf(COL_REFERENCE);
  const iRet  = headers.indexOf(COL_RETAILER);
  const iVint = headers.indexOf(COL_VINTAGE);
  const iPrc  = headers.indexOf(COL_PRICE);
  const iDay  = headers.indexOf(COL_DAY);

  // Filter rows matching selected retailer + reference
  const rows = raw.slice(1).filter(r =>
    r[iRet] === retailer && r[iRef] === reference && r[iPrc] !== "" && r[iPrc] !== null
  );

  if (rows.length === 0) {
    dataSheet.clearContents();
    dash.getCharts().forEach(c => dash.removeChart(c));
    return;
  }

  // Collect unique dates and vintages
  const dateSet    = new Set();
  const vintageSet = new Set();
  rows.forEach(r => {
    const day = normaliseDate_(r[iDay]);
    if (day) dateSet.add(day);
    if (r[iVint]) vintageSet.add(String(r[iVint]));
  });

  const dates    = [...dateSet].sort();
  const vintages = [...vintageSet].sort();

  // Build pivot: map[date][vintage] = price
  const pivot = {};
  rows.forEach(r => {
    const day = normaliseDate_(r[iDay]);
    if (!day) return;
    const vint = String(r[iVint]);
    if (!pivot[day]) pivot[day] = {};
    // Keep the most recent value if multiple records exist for same day+vintage
    pivot[day][vint] = Number(r[iPrc]);
  });

  // Write pivot to ChartData tab
  dataSheet.clearContents();
  const colHeaders = ["Date", ...vintages];
  dataSheet.getRange(1, 1, 1, colHeaders.length).setValues([colHeaders]);
  dataSheet.getRange(1, 1, 1, colHeaders.length).setFontWeight("bold");

  const pivotRows = dates.map(d => [
    d,
    ...vintages.map(v => (pivot[d] && pivot[d][v] != null ? pivot[d][v] : ""))
  ]);
  dataSheet.getRange(2, 1, pivotRows.length, colHeaders.length).setValues(pivotRows);
  dataSheet.getRange(2, 1, pivotRows.length, 1).setNumberFormat("yyyy-mm-dd");

  // Remove old charts from Dashboard
  dash.getCharts().forEach(c => dash.removeChart(c));

  // Build new chart anchored to Dashboard from ChartData range
  const dataRange = dataSheet.getRange(1, 1, pivotRows.length + 1, colHeaders.length);

  const chart = dash.newChart()
    .setChartType(Charts.ChartType.LINE)
    .addRange(dataRange)
    .setOption("title", `${reference} — ${retailer}`)
    .setOption("legend", { position: "right" })
    .setOption("hAxis", {
      title: "Date",
      format: "MMM yy",
      slantedText: true,
      slantedTextAngle: 45
    })
    .setOption("vAxis", { title: "Price (€)", minValue: 0 })
    .setOption("width",  1100)
    .setOption("height", 500)
    .setOption("interpolateNulls", true)
    .setOption("curveType", "none")
    .setPosition(4, 1, 0, 0)
    .build();

  dash.insertChart(chart);
}


// ── Helpers ───────────────────────────────────────────────────────────────────
function getUniqueValues_(ss) {
  const source  = ss.getSheetByName(SRC_TAB);
  const raw     = source.getDataRange().getValues();
  const headers = raw[0];
  const iRef = headers.indexOf(COL_REFERENCE);
  const iRet = headers.indexOf(COL_RETAILER);

  const retailers  = new Set();
  const references = new Set();
  raw.slice(1).forEach(r => {
    if (r[iRet]) retailers.add(String(r[iRet]));
    if (r[iRef]) references.add(String(r[iRef]));
  });

  return {
    retailers:  [...retailers].sort(),
    references: [...references].sort(),
  };
}

function normaliseDate_(val) {
  if (!val) return null;
  if (val instanceof Date) return val.toISOString().split("T")[0];
  const s = String(val).trim();
  // Accept YYYY-MM-DD or DD/MM/YYYY
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  if (/^\d{2}\/\d{2}\/\d{4}$/.test(s)) {
    const [d, m, y] = s.split("/");
    return `${y}-${m}-${d}`;
  }
  return null;
}
