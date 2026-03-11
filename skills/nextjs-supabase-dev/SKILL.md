---
name: nextjs-supabase-dev
description: >
  Desarrolla aplicaciones Next.js 15 con Supabase. App Router, Server Components,
  Server Actions, auth con Supabase Auth, queries con pgvector y RLS policies.
  Activar cuando se necesite crear o modificar apps Next.js + Supabase.
license: MIT
metadata:
  version: "1.0.0"
  category: fullstack
x-tools-required:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
x-system-prompt-addition: |
  Cuando trabajes con Next.js + Supabase:
  - Usa siempre App Router (no Pages Router)
  - Server Components por defecto, 'use client' solo cuando necesario
  - Crea cliente Supabase con createServerComponentClient para SSR
  - Row Level Security (RLS) ACTIVADO en TODAS las tablas
  - Variables de entorno: NEXT_PUBLIC_SUPABASE_URL y NEXT_PUBLIC_SUPABASE_ANON_KEY
  - Usa TypeScript estricto, genera tipos con supabase gen types
  - Tailwind CSS para estilos, shadcn/ui para componentes
---

# Next.js 15 + Supabase Development

## Setup inicial

```bash
npx create-next-app@latest mi-app --typescript --tailwind --app
cd mi-app
npm install @supabase/supabase-js @supabase/ssr
```

## Cliente Supabase para Server Components

```typescript
// lib/supabase/server.ts
import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'

export async function createClient() {
  const cookieStore = await cookies()
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() { return cookieStore.getAll() },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options))
        },
      },
    }
  )
}
```

## Server Action con validación

```typescript
// app/actions.ts
'use server'
import { createClient } from '@/lib/supabase/server'
import { revalidatePath } from 'next/cache'

export async function createItem(formData: FormData) {
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('No autenticado')

  const { error } = await supabase
    .from('items')
    .insert({ title: formData.get('title'), user_id: user.id })

  if (error) throw error
  revalidatePath('/')
}
```

## RLS Policy estándar

```sql
-- Habilitar RLS
ALTER TABLE items ENABLE ROW LEVEL SECURITY;

-- Usuario solo ve sus propios registros
CREATE POLICY "users_own_data" ON items
  FOR ALL USING (auth.uid() = user_id);
```
