// Minimal ECharts wrapper so components don't each import the whole library.
import * as echarts from 'echarts';

export function mountChart(el: HTMLElement, option: echarts.EChartsOption) {
  const chart = echarts.init(el);
  chart.setOption(option);
  const onResize = () => chart.resize();
  window.addEventListener('resize', onResize);
  return () => {
    window.removeEventListener('resize', onResize);
    chart.dispose();
  };
}
