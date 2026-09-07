nextflow.enable.dsl=2

process ANALYSIS_ADAPTER_SMOKE {
    output:
    path 'receipt.txt'

    script:
    '''
    printf 'nextflow-analysis-adapter-pass\n' > receipt.txt
    '''
}

workflow {
    ANALYSIS_ADAPTER_SMOKE()
}
