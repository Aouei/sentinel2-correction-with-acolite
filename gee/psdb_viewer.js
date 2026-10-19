/**
 * Earth Engine Code Editor script: view a pSDB band of one processed scene.
 *
 * How to use: open https://code.earthengine.google.com, create a new script, paste this file
 * and press Run. The asset lives in the ee-sergiohercar1 project, so it loads with your session.
 *
 * Edit the CONFIG block to look at another scene or band. The asset names are the ones the
 * pipeline writes: ETL/{date}_{tile}_{config hash}_{roi hash}_L2W.
 */

// ---------------------------------------------------------------------------------------------
// CONFIG
// ---------------------------------------------------------------------------------------------
var ASSET_ID = 'projects/ee-sergiohercar1/assets/ETL/2018-01-02_T29SPA_abcf7e790374_938f06a7a4a5_L2W';
var BAND = 'pSDB_green';  // 'pSDB_green' or 'pSDB_red'
var MIN = 0.8;
var MAX = 1.5;
// matplotlib viridis, 10 colors
var VIRIDIS = ['440154', '482878', '3E4A89', '31688E', '26828E',
               '1F9E89', '35B779', '6ECE58', 'B5DE2B', 'FDE725'];

// ---------------------------------------------------------------------------------------------
// MAP
// ---------------------------------------------------------------------------------------------
var image = ee.Image(ASSET_ID);

Map.centerObject(image, 11);
Map.addLayer(image.select(BAND), {min: MIN, max: MAX, palette: VIRIDIS}, BAND);

// ---------------------------------------------------------------------------------------------
// LEGEND
// ---------------------------------------------------------------------------------------------
var legend = ui.Panel({style: {position: 'bottom-left', padding: '8px 15px'}});

legend.add(ui.Label({value: BAND, style: {fontWeight: 'bold'}}));

legend.add(ui.Thumbnail({
  image: ee.Image.pixelLonLat().select(0),
  params: {
    bbox: [0, 0, 1, 0.1],
    dimensions: '200x20',
    format: 'png',
    min: 0,
    max: 1,
    palette: VIRIDIS
  },
  style: {stretch: 'horizontal', margin: '0 8px', maxHeight: '24px'}
}));

legend.add(ui.Panel([
  ui.Label(String(MIN), {margin: '4px 8px'}),
  ui.Label(String((MIN + MAX) / 2), {margin: '4px 8px', textAlign: 'center', stretch: 'horizontal'}),
  ui.Label(String(MAX), {margin: '4px 8px'})
], ui.Panel.Layout.flow('horizontal')));

Map.add(legend);
