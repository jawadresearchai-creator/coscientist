# dependencies.R -- the declared analysis stack.
#
# renv.lock is NOT hand-written. A lock file carries exact versions and package
# hashes; typing plausible-looking ones produces a file that fails to restore,
# or worse, restores something other than what it names. The lock is generated
# by snapshotting a real installation and is committed from that snapshot:
#
#   Rscript R/dependencies.R --install     # first time, or when adding a package
#   Rscript -e 'renv::snapshot()'          # writes renv.lock from what installed
#
# After that, renv.lock is authoritative and CI restores from it. This file
# stays as the record of WHY each package is here, which a lock file cannot say.

DEPENDENCIES <- list(

  # ---- data ----
  "data.table"   = "fast IO and by-reference aggregation; the panel builds live here",
  "arrow"        = "parquet; the lake's interchange format",
  "haven"        = "Stata/SPSS files, which is how a lot of finance data still arrives",
  "digest"       = "SHA-256 from R, so cos_data() can enforce the freeze itself",
  "jsonlite"     = "the results contract; emit.R writes through it",
  "dplyr"        = "readable transformation for the parts data.table makes opaque",
  "tidyr"        = "reshaping, mostly long/wide for event studies",

  # ---- core estimation ----
  "fixest"       = "high-dimensional FE, IV, and fast clustered SEs; the workhorse",
  "sandwich"     = "HC and HAC covariance",
  "lmtest"       = "coeftest, the standard wrapper for the above",
  "clubSandwich" = "CR2 small-sample cluster correction; matters below ~40 clusters",
  "fwildclusterboot" = "wild cluster bootstrap; the honest answer with few clusters",

  # ---- staggered adoption and parallel-trends robustness ----
  # The two-way FE estimator is biased under heterogeneous, staggered
  # treatment. A management-science panel is almost always staggered, so the
  # modern estimators are not optional extras here; TWFE alone is a referee's
  # first objection and a correct one.
  "did"          = "Callaway & Sant'Anna group-time ATT",
  "didimputation"= "Borusyak, Jaravel & Spiess imputation estimator",
  "did2s"        = "Gardner two-stage DiD",
  "HonestDiD"    = "Rambachan & Roth: how badly can parallel trends fail before the result dies",
  "synthdid"     = "Arkhangelsky et al. synthetic DiD, for few treated units",

  # ---- other designs ----
  "rdrobust"     = "RD with bias correction and robust CIs",
  "ivreg"        = "IV diagnostics fixest does not report",
  "AER"          = "weak-instrument tests",
  "plm"          = "panel unit roots and legacy panel estimators",

  # ---- heterogeneity and causal ML ----
  "grf"          = "causal forests for heterogeneous effects, pre-registered as exploratory",
  "DoubleML"     = "double/debiased ML when the control set is high-dimensional",

  # ---- Bayesian ----
  "cmdstanr"     = "Stan interface; compiles once and caches in CI",
  "posterior"    = "draws handling and convergence diagnostics",
  "bayesplot"    = "posterior predictive checks",

  # ---- reporting ----
  "broom"        = "tidy(); its column names ARE the emit contract's vocabulary",
  "modelsummary" = "regression tables that match journal conventions",
  "marginaleffects" = "contrasts and marginal effects, correctly delta-method'd",
  "ggplot2"      = "figures",
  "scales"       = "axis formatting",
  "patchwork"    = "multi-panel figure assembly",
  "renv"         = "the lock itself"
)

if (!interactive() && "--install" %in% commandArgs(TRUE)) {
  if (!requireNamespace("renv", quietly = TRUE)) install.packages("renv")
  pkgs <- setdiff(names(DEPENDENCIES), "renv")
  # cmdstanr is not on CRAN.
  pkgs <- setdiff(pkgs, "cmdstanr")
  renv::install(pkgs)
  renv::install("stan-dev/cmdstanr")
  message("installed; now run renv::snapshot() and commit renv.lock")
}
