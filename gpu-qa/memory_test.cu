// Bounded-kernel VRAM test. No driver settings, clock changes or device reset.
#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <chrono>
#include <algorithm>
#include <cmath>
#define CK(x) do { auto e=(x); if(e!=cudaSuccess){fprintf(stderr,"CUDA_ERROR %s\n",cudaGetErrorString(e));return 2;} } while(0)
__device__ uint32_t pattern(uint64_t i, int mode, uint32_t seed) {
 if(mode==0)return 0; if(mode==1)return ~0U; if(mode==2)return 0xaaaaaaaaU; if(mode==3)return 0x55555555U;
 if(mode<36)return 1U<<(mode-4);
 uint32_t x=uint32_t(i)^uint32_t(i>>32)*0x9e3779b9U^seed;
 x^=x>>16;x*=0x7feb352dU;x^=x>>15;x*=0x846ca68bU;x^=x>>16;return mode==37?~x:x;
}
__global__ void fill(uint32_t *p,uint64_t n,uint64_t offset,int mode,uint32_t seed) {
 for(uint64_t i=blockIdx.x*uint64_t(blockDim.x)+threadIdx.x;i<n;i+=uint64_t(gridDim.x)*blockDim.x)p[i]=pattern(offset+i,mode,seed);
}
__global__ void verify(uint32_t *p,uint64_t n,uint64_t offset,int mode,uint32_t seed,unsigned long long *errors) {
 __shared__ unsigned int counts[256]; unsigned int local=0;
 for(uint64_t i=blockIdx.x*uint64_t(blockDim.x)+threadIdx.x;i<n;i+=uint64_t(gridDim.x)*blockDim.x)local+=(p[i]!=pattern(offset+i,mode,seed));
 counts[threadIdx.x]=local;__syncthreads();
 for(int s=128;s;s>>=1){if(threadIdx.x<s)counts[threadIdx.x]+=counts[threadIdx.x+s];__syncthreads();}
 // At most one atomic per block even if every word is corrupt.
 if(threadIdx.x==0 && counts[0])atomicAdd(errors,(unsigned long long)counts[0]);
}
__global__ void corrupt(uint32_t*p){if(!threadIdx.x&&!blockIdx.x)p[0]^=1;}
int main(int argc,char**argv){
 double seconds=60,fraction=.90;size_t override_mib=0;bool inject=false;
 for(int i=1;i<argc;i++) {
  if(!strcmp(argv[i],"--inject-error")){inject=true;continue;}
  if(i+1>=argc){fprintf(stderr,"Missing option value\n");return 64;}
  if(!strcmp(argv[i],"--seconds"))seconds=atof(argv[++i]);
  else if(!strcmp(argv[i],"--fraction"))fraction=atof(argv[++i]);
  else if(!strcmp(argv[i],"--mib"))override_mib=strtoull(argv[++i],nullptr,10);
  else {fprintf(stderr,"Unknown option\n");return 64;}
 }
 if(!std::isfinite(seconds)||!std::isfinite(fraction)||seconds<=0||fraction<.5||fraction>.94)return 64;
 CK(cudaSetDevice(0));CK(cudaFree(0));cudaDeviceProp prop;CK(cudaGetDeviceProperties(&prop,0));
 size_t free,total;CK(cudaMemGetInfo(&free,&total));
 if(override_mib>free/(1024ULL*1024))return 64;
 size_t bytes=override_mib?override_mib*1024ULL*1024:(size_t)(free*fraction);
 if(bytes>free||bytes<4)return 64;bytes=bytes/4*4;
 uint32_t*p;unsigned long long*dev_errors;CK(cudaMalloc(&p,bytes));CK(cudaMalloc(&dev_errors,8));
 // Each kernel handles <=64 MiB regardless of card capacity or GPU count.
 const uint64_t chunk=64ULL*1024*1024/4, words=bytes/4;
 printf("{\"event\":\"allocation\",\"bytes\":%zu,\"total_bytes\":%zu,\"kernel_timeout_enabled\":%d,\"chunk_bytes\":%llu}\n",bytes,total,prop.kernelExecTimeoutEnabled,(unsigned long long)(chunk*4));fflush(stdout);
 auto start=std::chrono::steady_clock::now();unsigned long long passes=0;
 do {
  int mode=passes%38;uint32_t seed=1234567U+(passes/38)*7919U;CK(cudaMemset(dev_errors,0,8));
  for(uint64_t off=0;off<words;off+=chunk){uint64_t n=std::min(chunk,words-off);fill<<<4096,256>>>(p+off,n,off,mode,seed);CK(cudaGetLastError());CK(cudaDeviceSynchronize());}
  if(inject&&passes==0){corrupt<<<1,1>>>(p);CK(cudaGetLastError());CK(cudaDeviceSynchronize());}
  for(uint64_t off=0;off<words;off+=chunk){uint64_t n=std::min(chunk,words-off);verify<<<4096,256>>>(p+off,n,off,mode,seed,dev_errors);CK(cudaGetLastError());CK(cudaDeviceSynchronize());}
  unsigned long long errors;CK(cudaMemcpy(&errors,dev_errors,8,cudaMemcpyDeviceToHost));
  printf("{\"event\":\"memory_pass\",\"pass\":%llu,\"pattern\":%d,\"bytes_checked\":%zu,\"errors\":%llu}\n",passes,mode,bytes,errors);fflush(stdout);
  if(errors){cudaFree(dev_errors);cudaFree(p);return 3;}passes++;
 }while(passes<38||std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()<seconds);
 double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
 CK(cudaFree(dev_errors));CK(cudaFree(p));
 printf("{\"event\":\"complete\",\"stage\":\"memory\",\"active_seconds\":%.6f,\"checks\":%llu,\"bytes_checked_per_pass\":%zu}\n",elapsed,passes,bytes);return 0;
}
