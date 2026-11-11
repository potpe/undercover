import { createClient } from "@supabase/supabase-js";

function environment(name: string): string | undefined {
  const values = import.meta.env as unknown as Record<string, unknown>;
  const value = values[name];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

const url = environment("VITE_SUPABASE_URL") ?? "http://127.0.0.1:8005";
const key = environment("VITE_SUPABASE_PUBLISHABLE_KEY") ?? "test-key";

export const supabase = createClient(url, key);
