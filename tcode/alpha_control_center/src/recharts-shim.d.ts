// Shim so TypeScript compiles before recharts is installed.
// Once `npm install recharts` completes this file can be removed.
declare module 'recharts' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const BarChart: any, Bar: any, XAxis: any, YAxis: any, Tooltip: any,
    ResponsiveContainer: any, PieChart: any, Pie: any, Cell: any, Legend: any;
  export { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend };
}
