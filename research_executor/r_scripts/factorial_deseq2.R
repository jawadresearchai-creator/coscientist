args <- commandArgs(trailingOnly=TRUE)
if (length(args) != 5) stop("expected counts.tsv samples.tsv outdir filter_total alpha")
counts_file <- args[1]
samples_file <- args[2]
outdir <- args[3]
filter_total <- as.integer(args[4])
alpha <- as.numeric(args[5])

suppressPackageStartupMessages(library(DESeq2))

counts_df <- read.delim(counts_file, check.names=FALSE, stringsAsFactors=FALSE)
if (!("gene_id" %in% names(counts_df))) stop("counts matrix lacks gene_id")
rownames(counts_df) <- counts_df$gene_id
counts_df$gene_id <- NULL
counts <- as.matrix(counts_df)
storage.mode(counts) <- "integer"

samples <- read.delim(samples_file, check.names=FALSE, stringsAsFactors=FALSE)
rownames(samples) <- samples$sample_id
samples <- samples[colnames(counts), , drop=FALSE]
if (!all(rownames(samples) == colnames(counts))) stop("sample/count order mismatch")
samples$prior_ja <- factor(samples$prior_ja, levels=c("W","JA"))
samples$challenge_ja <- factor(samples$challenge_ja, levels=c("W","JA"))

dds <- DESeqDataSetFromMatrix(
  countData=counts,
  colData=samples,
  design=~ prior_ja + challenge_ja + prior_ja:challenge_ja
)
keep <- rowSums(counts(dds)) >= filter_total
dds <- dds[keep,]
if (nrow(dds) < 100) stop("too few genes after low-count filtering")
dds <- DESeq(dds, quiet=TRUE)

rn <- resultsNames(dds)
writeLines(rn, file.path(outdir, "results_names.txt"))
prior_name <- rn[grepl("^prior_ja_JA_vs_W$", rn)]
challenge_name <- rn[grepl("^challenge_ja_JA_vs_W$", rn)]
interaction_name <- rn[grepl("prior_jaJA.*challenge_jaJA|challenge_jaJA.*prior_jaJA", rn)]
if (length(prior_name)!=1 || length(challenge_name)!=1 || length(interaction_name)!=1) {
  stop(paste("unexpected DESeq2 coefficient names:", paste(rn, collapse=", ")))
}

write_result <- function(res, filename) {
  df <- as.data.frame(res)
  df$gene_id <- rownames(df)
  df <- df[, c("gene_id", setdiff(names(df), "gene_id"))]
  ord <- order(is.na(df$padj), df$padj, -abs(df$log2FoldChange), na.last=TRUE)
  df <- df[ord,]
  write.table(df, file.path(outdir, filename), sep="\t", quote=FALSE, row.names=FALSE, na="NA")
}

res_wja <- results(dds, name=challenge_name, alpha=alpha)
res_jaw <- results(dds, name=prior_name, alpha=alpha)
res_jaja <- results(dds, contrast=list(c(challenge_name, interaction_name)), alpha=alpha)
res_int <- results(dds, name=interaction_name, alpha=alpha)
write_result(res_wja, "W_JA_vs_W_W.tsv")
write_result(res_jaw, "JA_W_vs_W_W.tsv")
write_result(res_jaja, "JA_JA_vs_JA_W.tsv")
write_result(res_int, "interaction.tsv")

vsd <- vst(dds, blind=FALSE)
pca <- prcomp(t(assay(vsd)))
percent <- (pca$sdev^2 / sum(pca$sdev^2)) * 100
pca_df <- data.frame(
  sample_id=rownames(pca$x),
  PC1=pca$x[,1],
  PC2=pca$x[,2],
  group=samples[rownames(pca$x),"group"],
  prior_ja=samples[rownames(pca$x),"prior_ja"],
  challenge_ja=samples[rownames(pca$x),"challenge_ja"]
)
write.table(pca_df, file.path(outdir, "pca_coordinates.tsv"), sep="\t", quote=FALSE, row.names=FALSE)

png(file.path(outdir, "pca.png"), width=1800, height=1400, res=220)
cols <- c(W_W="#000000", W_JA="#1f77b4", JA_W="#2ca02c", JA_JA="#d62728")
pch <- c(W_W=16, W_JA=17, JA_W=15, JA_JA=18)
plot(pca_df$PC1, pca_df$PC2,
     xlab=sprintf("PC1 (%.1f%%)", percent[1]),
     ylab=sprintf("PC2 (%.1f%%)", percent[2]),
     col=cols[pca_df$group], pch=pch[pca_df$group], cex=1.4)
text(pca_df$PC1, pca_df$PC2, labels=pca_df$sample_id, pos=3, cex=0.65)
legend("topright", legend=names(cols), col=cols, pch=pch, bty="n")
dev.off()

capture.output(sessionInfo(), file=file.path(outdir, "deseq2_sessionInfo.txt"))
