function Kg = assembleGeometricStiffnessMatrix(mesh,forces,element_data,dof)
%ASSEMBLEGEOMETRICSTIFFNESSMATRIX Approximate stress-stiffening matrix.
%
% Uses the recovered axial force N as tensile beam-column geometric
% stiffness in the two bending planes. This captures first-order centrifugal
% stiffening but does not include gyroscopic coupling.

if nargin < 4 || isempty(dof)
    dof.ndof_node = 6;
    dof.total_dofs = 6*mesh.n_nodes;
end

Kg = zeros(dof.total_dofs,dof.total_dofs);
N_station = forces.N_N(:);

for e = 1:mesh.n_elements
    n1 = mesh.connectivity(e,1);
    n2 = mesh.connectivity(e,2);
    L = element_data(e).L_m;
    P = mean(N_station([n1 n2]));
    P = max(P,0);
    if P <= 0
        continue
    end

    kg_plane = P/(30*L)*[ ...
        36,    3*L,  -36,    3*L; ...
        3*L, 4*L^2, -3*L, -L^2; ...
       -36,   -3*L,   36,   -3*L; ...
        3*L, -L^2,  -3*L, 4*L^2];

    kg_local = zeros(12,12);
    kg_local([2 6 8 12],[2 6 8 12]) = kg_plane;
    S = diag([1 -1 1 -1]);
    kg_local([3 5 9 11],[3 5 9 11]) = S*kg_plane*S;

    edofs = element_data(e).edofs;
    Kg(edofs,edofs) = Kg(edofs,edofs) + element_data(e).T'*kg_local*element_data(e).T;
end

Kg = (Kg + Kg')/2;

end
