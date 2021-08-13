#include <torch/torch.h>
#include <torch/script.h> // One-stop header.

#include <starkiller.H>

#include <AMReX_ParmParse.H>
#include <AMReX_VisMF.H>

#include <iostream>
#include <memory>

using namespace amrex;


int main (int argc, char* argv[])
{
    amrex::Initialize(argc, argv);

    {
        int n_cell = 128;
        int max_grid_size = 32;

	std::string model_filename = "my_model.pt";
	
        Real dens = 1.0e8;
        Real temp = 4.0e8;
	Real end_time = 1.0e-6;

        // read parameters
        {
            ParmParse pp;
            pp.query("n_cell", n_cell);
            pp.query("max_grid_size", max_grid_size);
	    pp.query("model_file", model_filename);
            pp.query("density", dens);
            pp.query("temperature", temp);
	    pp.query("end_time", end_time);
        }

        // Initial mass fraction
        Real xhe = 1.0;
	
        /////////// GENERATING MULTIFAB DATASET ///////////////////////////////////////

        // initialize arbitrary grid
        Geometry geom;
        {
            RealBox rb({AMREX_D_DECL(0.0,0.0,0.0)}, {AMREX_D_DECL(1.0,1.0,1.0)}); // physical domain
            Array<int,AMREX_SPACEDIM> is_periodic{AMREX_D_DECL(false, false, false)};
            Geometry::Setup(&rb, 0, is_periodic.data());
            Box domain(IntVect(0), IntVect(n_cell-1));
            geom.define(domain);
        }
        BoxArray ba(geom.Domain());
        ba.maxSize(max_grid_size);
        DistributionMapping dm{ba};
	
        // initialize training multifabs
        ReactionSystem system;
        system.init(ba, dm);
        system.init_state(dens, temp, xhe, end_time/*,true*/);

        // Make a copy of input multifab (training)
	MultiFab input(ba, dm, NSCAL, 0);
	MultiFab::Copy(input, system.state, 0, 0, NSCAL, 0);

	VisMF::Write(input, "test_data_mf");
        Print() << "Initializing input multifab complete." << std::endl;

        // retrieve size of multifab
        const auto nbox = geom.Domain().bigEnd();
        
        // // Copy input multifab to torch tensor
#if AMREX_SPACEDIM == 2
	at::Tensor t1 = torch::zeros({(nbox[0]+1)*(nbox[1]+1), NSCAL});
#elif AMREX_SPACEDIM == 3
        at::Tensor t1 = torch::zeros({(nbox[0]+1)*(nbox[1]+1)*(nbox[2]+1), NSCAL});
#endif
        
#ifdef USE_AMREX_CUDA
        t1 = t1.to(torch::kCUDA);
#endif

#ifdef _OPENMP
#pragma omp parallel if (Gpu::notInLaunchRegion())
#endif
        for (MFIter mfi(input, TilingIfNotGPU()); mfi.isValid(); ++mfi)
        {
            const Box& tileBox = mfi.tilebox();
            auto const& input_arr = input.array(mfi);

            ParallelFor(tileBox, NSCAL,
			[=] AMREX_GPU_DEVICE(int i, int j, int k, int n) {
                const int index = AMREX_SPACEDIM == 2 ?
                    i*(nbox[1]+1)+j : (i*(nbox[1]+1)+j)*(nbox[2]+1)+k;
                t1[index][n] = input_arr(i, j, k, n);
          });
        }

        // Load pytorch module via torch script
        torch::jit::script::Module module;
        try {
            // Deserialize the ScriptModule from a file using torch::jit::load().
            module = torch::jit::load(model_filename);
        }
        catch (const c10::Error& e) {
            std::cerr << "error loading the model\n";
            return -1;
        }

        std::cout << "Model loaded.\n";

        // Evaluate torch data
        std::vector<torch::jit::IValue> inputs_torch{t1};
        at::Tensor outputs_torch = module.forward(inputs_torch).toTensor();
        std::cout << "example output: "
                  << outputs_torch.slice(/*dim=*/0, /*start=*/0, /*end=*/5) << '\n';
#ifdef USE_AMREX_CUDA
        outputs_torch = outputs_torch.to(torch::kCUDA);
#endif

        // Copy torch tensor to output multifab
        MultiFab output(ba, dm, 2, 0);

#ifdef _OPENMP
#pragma omp parallel if (Gpu::notInLaunchRegion())
#endif
        for (MFIter mfi(output, TilingIfNotGPU()); mfi.isValid(); ++mfi)
        {
            const Box& tileBox = mfi.tilebox();
            auto const& output_arr = output.array(mfi);

            ParallelFor(tileBox, [=] AMREX_GPU_DEVICE(int i, int j, int k) {
                const int index = AMREX_SPACEDIM == 2 ?
                    i*(nbox[1]+1)+j : (i*(nbox[1]+1)+j)*(nbox[2]+1)+k;
                output_arr(i, j, k, 0) = outputs_torch[index][0].item<double>();
                output_arr(i, j, k, 1) = outputs_torch[index][1].item<double>();
          });
        }
        VisMF::Write(output, "output_mf");

        Print() << "Model evaluation complete." << std::endl;

	
        // compute training solutions
        MultiFab y;
        MultiFab ydot;
        system.sol(y);
        system.rhs(y, ydot);

	//std::cout<<"Tensor example from PYTORCH!"<<std::endl;
        //torch::Tensor tensor = torch::rand({2, 3});
        //std::cout << tensor << std::endl;



    }

    amrex::Finalize();
}
