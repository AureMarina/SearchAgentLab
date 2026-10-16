/**
 * Adapted from @fnando/sparkline, Copyright (c) 2018 Nando Vieira, MIT.
 * Upstream: github.com/fnando/sparkline/src/sparkline.js
 * Source blob: 1313b2e2f2b7411b2bd1b3e19b627d2eb83296b6
 * Local changes: optional xMap/yMap for real step and signed metric scales;
 * root child removal safety; SVG-coordinate mouse input; max=0 fallback.
 * Caller splits missing-data segments; evaluation dots are never connected.
 * Full notice in sparkline.LICENSE. No external requests or dependencies.
 */
function getY(max, height, diff, value) {
  return parseFloat((height - (value * height / (max || 1)) + diff).toFixed(2));
}
function removeChildren(svg) { while (svg.firstChild) svg.removeChild(svg.firstChild); }
function defaultFetch(entry) { return entry.value; }
function buildElement(tag, attrs) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const name in attrs) element.setAttribute(name, attrs[name]);
  return element;
}
export function sparkline(svg, entries, options) {
  removeChildren(svg);
  if (entries.length <= 1) return;
  options = options || {};
  if (typeof entries[0] === "number") entries = entries.map(value => ({value}));
  const onmousemove = options.onmousemove;
  const onmouseout = options.onmouseout;
  const interactive = ("interactive" in options) ? options.interactive : !!onmousemove;
  const spotRadius = options.spotRadius || 2;
  const spotDiameter = spotRadius * 2;
  const cursorWidth = options.cursorWidth || 2;
  const strokeWidth = parseFloat(svg.attributes["stroke-width"].value);
  const fetch = options.fetch || defaultFetch;
  const values = entries.map(entry => fetch(entry));
  const width = parseFloat(svg.attributes.width.value) - spotDiameter * 2;
  const fullHeight = parseFloat(svg.attributes.height.value);
  const height = fullHeight - (strokeWidth * 2) - spotDiameter;
  const max = Math.max(...values);
  const offscreen = -1000;
  const lastItemIndex = values.length - 1;
  const offset = width / lastItemIndex;
  const datapoints = [];
  let pathCoords = "";
  values.forEach((value, index) => {
    const x = options.xMap ? options.xMap(entries[index], index) : index * offset + spotDiameter;
    const y = options.yMap ? options.yMap(entries[index], index) : getY(max, height, strokeWidth + spotRadius, value);
    datapoints.push(Object.assign({}, entries[index], {index, x, y}));
    pathCoords += `${index ? " L" : "M"} ${x} ${y}`;
  });
  const path = buildElement("path", {class: "sparkline--line", d: pathCoords, fill: "none"});
  const fillCoords = `${pathCoords} V ${fullHeight} L ${spotDiameter} ${fullHeight} Z`;
  const fill = buildElement("path", {class: "sparkline--fill", d: fillCoords, stroke: "none"});
  svg.appendChild(fill);svg.appendChild(path);
  if (!interactive) return;
  const cursor = buildElement("line", {class:"sparkline--cursor", x1:offscreen, x2:offscreen, y1:0, y2:fullHeight, "stroke-width":cursorWidth});
  const spot = buildElement("circle", {class:"sparkline--spot", cx:offscreen, cy:offscreen, r:spotRadius});
  svg.appendChild(cursor);svg.appendChild(spot);
  const interactionLayer = buildElement("rect", {width:svg.attributes.width.value, height:svg.attributes.height.value, style:"fill:transparent;stroke:transparent", class:"sparkline--interaction-layer"});
  svg.appendChild(interactionLayer);
  interactionLayer.addEventListener("mouseout", event => {
    cursor.setAttribute("x1",offscreen);cursor.setAttribute("x2",offscreen);spot.setAttribute("cx",offscreen);
    if (onmouseout) onmouseout(event);
  });
  interactionLayer.addEventListener("mousemove", event => {
    const pt=svg.createSVGPoint();pt.x=event.clientX;pt.y=event.clientY;
    const mouseX=pt.matrixTransform(svg.getScreenCTM().inverse()).x;
    let nextDataPoint=datapoints.find(entry=>entry.x>=mouseX)||datapoints[lastItemIndex];
    const previousDataPoint=datapoints[datapoints.indexOf(nextDataPoint)-1];
    const currentDataPoint=previousDataPoint&&mouseX<previousDataPoint.x+(nextDataPoint.x-previousDataPoint.x)/2?previousDataPoint:nextDataPoint;
    spot.setAttribute("cx",currentDataPoint.x);spot.setAttribute("cy",currentDataPoint.y);
    cursor.setAttribute("x1",currentDataPoint.x);cursor.setAttribute("x2",currentDataPoint.x);
    if(onmousemove)onmousemove(event,currentDataPoint);
  });
}
export default sparkline;
