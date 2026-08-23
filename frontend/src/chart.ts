// ECharts, with only what the timeline draws (the markers are `graphic`, T3) - loaded the first time a
// timeline card renders, so the module every page loads stays small (D12 §5.5).

import { BarChart, CustomChart, LineChart } from "echarts/charts";
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
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  CustomChart,
  LineChart,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export { echarts };
