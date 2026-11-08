export interface ResearchFile { path: string; directory: boolean }
export interface Cell { cell: string; value: string | number | boolean | null; kind: string; format: string }
export interface Workbook { version: string; sheets: { name: string; rows: Cell[][] }[]; warnings: string[] }
export interface CellChange { sheet: string; cell: string; value: Cell['value']; kind: string }
export interface ResearchAPI {
  context(): Promise<{fileTest: boolean}>;
  list(projectId: string): Promise<ResearchFile[]>;
  read(projectId: string, path: string): Promise<string>;
  workbook(projectId: string, path: string): Promise<Workbook>;
  save(projectId: string, path: string, version: string, changes: CellChange[]): Promise<Workbook | null>;
}
declare global { interface Window { research: ResearchAPI } }
