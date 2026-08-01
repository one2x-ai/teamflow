<script lang="ts">
  import MemoryList from "./routes/MemoryList.svelte";
  import MemoryDetail from "./routes/MemoryDetail.svelte";

  let route = $state(
    typeof window !== "undefined" && window.location.pathname === "/memory"
      ? "detail"
      : "list",
  );
</script>

{#if route === "detail"}
  <MemoryDetail />
{:else}
  <MemoryList />
{/if}
