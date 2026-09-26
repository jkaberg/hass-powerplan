// ECharts, with only what the timeline draws (the markers are `graphic`, T3) - loaded the first time a
// timeline card renders, so the module every page loads stays small (D12 §5.5).

import { BarChart, CustomChart, LineChart, ScatterChart } from "echarts/charts";
import {
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer, SVGRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  CustomChart,
  LineChart,
  // History's held-back marks (D12 §5.21 H2).
  ScatterChart,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  CanvasRenderer,
  // Now's Plan card draws in SVG (iteration 4, `timeline-plan-mode.ts`).
  SVGRenderer,
]);

export { echarts };
