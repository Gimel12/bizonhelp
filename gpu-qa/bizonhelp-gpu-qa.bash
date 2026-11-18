# Sourced after existing bizonhelp definitions. Re-sourcing does not duplicate the help.
if [[ -z ${BIZON_GPU_QA_HELP_LOADED:-} ]]; then
    if alias bizonhelp >/dev/null 2>&1; then
        _bizon_qa_alias=$(alias bizonhelp)
        eval "${_bizon_qa_alias/alias bizonhelp=/alias _bizonhelp_before_gpu_qa=}"
        unalias bizonhelp
        _bizonhelp_original_qa() { eval '_bizonhelp_before_gpu_qa'; }
        unset _bizon_qa_alias
    elif declare -F bizonhelp >/dev/null; then
        eval "$(declare -f bizonhelp | sed '1s/bizonhelp/_bizonhelp_original_qa/')"
    else
        _bizonhelp_original_qa() { :; }
    fi
    function bizonhelp {
        _bizonhelp_original_qa "$@"
        printf '\nGPU QA (candidate; hardware qualification pending)\n'
        printf '  gpu-qa validate --gpus all       Short tool-validation pilot\n'
        printf '  gpu-qa quick --gpus all          ~30 min per GPU screening\n'
        printf '  gpu-qa production --pilot        ~8 active hours per GPU\n'
        printf '  gpu-qa rma --pilot               ~24 active hours per GPU\n'
        printf '  gpu-qa status / report / export-rma / stop\n'
        printf '  Select cards with --gpus 0,1 or full GPU UUIDs (1-8 cards).\n'
    }
    BIZON_GPU_QA_HELP_LOADED=1
fi
