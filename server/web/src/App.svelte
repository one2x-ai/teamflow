<script lang="ts">
  import Button from "$lib/components/ui/button/button.svelte";
  import Card from "$lib/components/ui/card/card.svelte";
  import CardContent from "$lib/components/ui/card/card-content.svelte";
  import CardHeader from "$lib/components/ui/card/card-header.svelte";
  import CardTitle from "$lib/components/ui/card/card-title.svelte";

  let count = $state(0);
</script>

<main class="flex min-h-screen items-center justify-center bg-gray-50 p-8">
  <Card class="w-full max-w-md">
    <CardHeader>
      <CardTitle>Teamflow Console</CardTitle>
    </CardHeader>
    <CardContent>
      <p class="text-sm text-gray-600 mb-4">
        Phase B placeholder — toolchain verification.
      </p>
      <Button onclick={() => count++}>
        Clicked {count} times
      </Button>
    </CardContent>
  </Card>
</main>
