<script lang="ts">
  import { onMount } from "svelte";
  import { fetchMemory } from "$lib/api";
  import type { MemoryNote } from "$lib/types";
  import Card from "$lib/components/ui/card/card.svelte";
  import CardContent from "$lib/components/ui/card/card-content.svelte";
  import CardHeader from "$lib/components/ui/card/card-header.svelte";
  import CardTitle from "$lib/components/ui/card/card-title.svelte";
  import Button from "$lib/components/ui/button/button.svelte";

  let note = $state<MemoryNote | null>(null);
  let loading = $state(true);
  let error = $state(false);

  const permalink =
    new URLSearchParams(window.location.search).get("permalink") || "";

  async function load() {
    loading = true;
    error = false;
    try {
      note = await fetchMemory(permalink);
    } catch {
      error = true;
    } finally {
      loading = false;
    }
  }

  onMount(load);
</script>

<main class="mx-auto w-full max-w-3xl px-4 py-8">
  <a href="/" class="mb-4 inline-block">
    <Button variant="ghost">Back to memories</Button>
  </a>

  {#if loading}
    <p class="text-sm text-muted-foreground">Loading memory...</p>
  {:else if error}
    <div class="py-12 text-center text-muted-foreground">
      Failed to load this memory. Please try again.
    </div>
  {:else if note}
    <Card>
      <CardHeader>
        <CardTitle>{note.title || "Untitled"}</CardTitle>
      </CardHeader>
      <CardContent>
        <div
          class="prose prose-sm max-w-none whitespace-pre-wrap text-foreground"
        >
          {note.content || ""}
        </div>
      </CardContent>
    </Card>
  {/if}
</main>
