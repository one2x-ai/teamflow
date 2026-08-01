<script lang="ts">
  import { onMount } from "svelte";
  import { fetchMemories } from "$lib/api";
  import type { MemoryItem } from "$lib/types";
  import Card from "$lib/components/ui/card/card.svelte";
  import CardContent from "$lib/components/ui/card/card-content.svelte";
  import CardHeader from "$lib/components/ui/card/card-header.svelte";
  import CardTitle from "$lib/components/ui/card/card-title.svelte";
  import Badge from "$lib/components/ui/badge/badge.svelte";
  import Input from "$lib/components/ui/input/input.svelte";
  import Button from "$lib/components/ui/button/button.svelte";
  import Skeleton from "$lib/components/ui/skeleton/skeleton.svelte";

  let items = $state<MemoryItem[]>([]);
  let loading = $state(true);
  let error = $state(false);
  let query = $state("");
  let searchInput = $state("");
  let page = $state(1);
  let totalPages = $state(1);

  async function load() {
    loading = true;
    error = false;
    try {
      const data = await fetchMemories({ page, query });
      items = data.items;
      totalPages = data.total_pages;
    } catch {
      error = true;
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function handleSearch(e: SubmitEvent) {
    e.preventDefault();
    query = searchInput;
    page = 1;
    load();
  }

  function clearSearch() {
    searchInput = "";
    query = "";
    page = 1;
    load();
  }

  function prevPage() {
    if (page > 1) {
      page--;
      load();
    }
  }

  function nextPage() {
    if (page < totalPages) {
      page++;
      load();
    }
  }

  function getTitle(item: MemoryItem): string {
    return item.title || "Untitled";
  }

  function getType(item: MemoryItem): string {
    return item.type || "note";
  }

  function getExcerpt(item: MemoryItem): string {
    const text = item.content || item.body || item.summary || item.text || "";
    return text.length > 240 ? text.slice(0, 240) + "..." : text;
  }
</script>

<main class="mx-auto w-full max-w-4xl px-4 py-8">
  <header class="mb-6">
    <h1 class="mb-4 text-2xl font-bold">Teamflow Memory</h1>
    <form class="flex flex-wrap gap-2" onsubmit={handleSearch}>
      <Input
        type="search"
        name="query"
        placeholder="Search memories"
        bind:value={searchInput}
      />
      <Button type="submit">Search</Button>
      {#if query}
        <Button type="button" variant="outline" onclick={clearSearch}>Clear</Button>
      {/if}
    </form>
  </header>

  {#if loading}
    <div class="space-y-4">
      <p class="text-sm text-muted-foreground">Loading memories...</p>
      {#each Array(3) as _, i (i)}
        <Skeleton class="h-24 w-full" />
      {/each}
    </div>
  {:else if error}
    <div class="py-12 text-center text-muted-foreground">
      Failed to load memories. Please try again.
    </div>
  {:else if items.length === 0}
    <div class="py-12 text-center text-muted-foreground">
      No memories found.
    </div>
  {:else}
    <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
      {#each items as item (item.permalink)}
        <Card>
          <CardHeader>
            <div class="flex items-center gap-2">
              <Badge variant="secondary">{getType(item)}</Badge>
              <a
                href="/memory?permalink={encodeURIComponent(item.permalink || '')}"
                class="hover:underline"
              >
                <CardTitle>{getTitle(item)}</CardTitle>
              </a>
            </div>
          </CardHeader>
          {@const excerpt = getExcerpt(item)}
          {#if excerpt}
            <CardContent>
              <p class="text-sm text-muted-foreground">{excerpt}</p>
            </CardContent>
          {/if}
        </Card>
      {/each}
    </div>

    <nav
      class="mt-8 flex items-center justify-center gap-4"
      aria-label="Pagination"
    >
      <Button onclick={prevPage} disabled={page <= 1} variant="outline">
        Previous
      </Button>
      <span class="text-sm text-muted-foreground">
        Page {page} of {totalPages}
      </span>
      <Button onclick={nextPage} disabled={page >= totalPages} variant="outline">
        Next
      </Button>
    </nav>
  {/if}
</main>
